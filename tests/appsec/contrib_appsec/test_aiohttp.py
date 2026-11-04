import asyncio
import base64
import binascii
import contextlib
import gzip
from http.cookies import SimpleCookie
import itertools
import json
import time
import traceback
from typing import Any
from typing import Optional
from typing import Union
from unittest import mock
import urllib.parse
import zlib

from aiohttp import ClientError
from aiohttp import ClientSession
from aiohttp import MultipartReader
from aiohttp import web
import aiohttp.connector
from aiohttp.test_utils import TestServer
from aiohttp.test_utils import make_mocked_request
from multidict import CIMultiDict
import pytest
from yarl import URL

from ddtrace.appsec import _common_module_patches
from ddtrace.appsec._asm_request_context import _RASP_SUBCONTEXT
from ddtrace.appsec._common_module_patches import _SsrfAiohttpConnect
from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.aiohttp import _multipart as aiohttp_multipart
from ddtrace.appsec._contrib.aiohttp import _utils as aiohttp_utils
from ddtrace.appsec._utils import get_triggers
from ddtrace.contrib.internal.aiohttp.middlewares import _restore_request_body
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.packages import get_version_for_package
from tests.appsec import rules
from tests.appsec.contrib_appsec import utils
from tests.appsec.contrib_appsec.aiohttp_app.app import _STORED_RESPONSES
from tests.appsec.contrib_appsec.aiohttp_app.app import get_app
from tests.appsec.contrib_appsec.api10_server import API10_RESPONSE_BODY
from tests.utils import override_global_config
from tests.utils import scoped_tracer


AIOHTTP_VERSION = tuple(int(v) for v in get_version_for_package("aiohttp").split(".")[:3] if v.isdigit())


class AiohttpResponse:
    def __init__(self, status: int, headers: dict[str, str], body: bytes):
        self.status = status
        self.headers = headers
        self.body = body


class AiohttpTestClient:
    def __init__(self, loop: asyncio.AbstractEventLoop, server: TestServer, interface: utils.Interface):
        self._loop = loop
        self._server = server
        self._interface = interface

    async def _request(self, method: str, path: str, **kwargs: Any) -> AiohttpResponse:
        headers = CIMultiDict(kwargs.pop("headers", None) or {})
        data: Optional[Any] = kwargs.pop("data", None)
        if isinstance(data, dict):
            data = urllib.parse.urlencode(data)
        if isinstance(data, str):
            data = data.encode("utf-8")
        if "content_type" in kwargs:
            headers["Content-Type"] = kwargs.pop("content_type")
        cookies = kwargs.pop("cookies", None)
        if cookies:
            cookie = SimpleCookie()
            for key, value in cookies.items():
                cookie[key] = value
            headers["Cookie"] = "; ".join(morsel.OutputString() for morsel in cookie.values())
        url = URL(f"http://localhost:{self._server.port}{path}", encoded=True)
        async with ClientSession() as session:
            async with session.request(method, url, data=data, headers=headers, allow_redirects=False) as response:
                body = await response.read()
                return AiohttpResponse(response.status, {k.lower(): v for k, v in response.headers.items()}, body)

    def _run(self, method: str, path: str, **kwargs: Any) -> AiohttpResponse:
        self._interface.SERVER_PORT = self._server.port
        return self._loop.run_until_complete(self._request(method, path, **kwargs))

    @property
    def loop(self) -> asyncio.AbstractEventLoop:
        return self._loop

    def settle(self, until=None, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        self._loop.run_until_complete(asyncio.sleep(0.05))
        while until is not None and not until() and time.monotonic() < deadline:
            self._loop.run_until_complete(asyncio.sleep(0.05))

    def get(self, path: str, **kwargs: Any) -> AiohttpResponse:
        return self._run("GET", path, **kwargs)

    def post(self, path: str, **kwargs: Any) -> AiohttpResponse:
        return self._run("POST", path, **kwargs)

    def options(self, path: str, **kwargs: Any) -> AiohttpResponse:
        return self._run("OPTIONS", path, **kwargs)


class _Test_Aiohttp_Base:
    @pytest.fixture
    def interface(self, printer):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        with scoped_tracer() as tracer:
            server = TestServer(get_app(), host="localhost")
            loop.run_until_complete(server.start_server())
            try:
                interface = utils.Interface("aiohttp", None, None)
                interface.client = AiohttpTestClient(loop, server, interface)
                interface.version = AIOHTTP_VERSION
                interface.tracer = tracer
                interface.printer = printer
                yield interface
            finally:
                loop.run_until_complete(server.close())
                loop.close()
                asyncio.set_event_loop(None)

    def status(self, response):
        return response.status

    def headers(self, response):
        return response.headers

    def body(self, response):
        return response.body.decode("utf-8")

    def location(self, response):
        return response.headers["location"]


_ATTACK = "yqrweytqwreasldhkuqwgervflnmlnli"


def _utf7(text: str) -> str:
    return "+" + base64.b64encode(text.encode("utf-16-be")).decode().rstrip("=") + "-"


def _multipart_body(fields: dict[str, Union[bytes, tuple[str, bytes]]]) -> tuple[bytes, str]:
    boundary = "52d1fb4eb9c021e53ac2846190e4ac72"
    body = b""
    for name, field in fields.items():
        headers, value = field if isinstance(field, tuple) else ("", field)
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"{headers}\r\n\r\n'.encode() + value + b"\r\n"
        )
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


def _deflate(data: bytes) -> bytes:
    compressor = zlib.compressobj(wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush()


def _differential_body(
    charset: Optional[str], content_type: str, transfer_encoding: str, compression: str, nested: bool
) -> tuple[bytes, str]:
    forms = {"application/json": f'{{"a": "{_ATTACK}"}}', "application/x-www-form-urlencoded": f"x={_ATTACK}"}
    data = forms.get(content_type, _ATTACK).encode(charset or "utf-8")
    data = {
        "": data,
        "gzip": gzip.compress(data),
        "gzip+junk": gzip.compress(data) + b"junk",
        "two-gzip": gzip.compress(b"benign ") + gzip.compress(data),
        "two-deflate": _deflate(b"benign ") + _deflate(data),
    }[compression]
    if transfer_encoding == "base64":
        data = base64.b64encode(data)
    elif transfer_encoding == "quoted-printable":
        data = binascii.b2a_qp(data)
    headers = f"\r\nContent-Type: {content_type}" + (f"; charset={charset}" if charset else "")
    if transfer_encoding:
        headers += f"\r\nContent-Transfer-Encoding: {transfer_encoding}"
    if compression:
        headers += "\r\nContent-Encoding: " + ("deflate" if compression == "two-deflate" else "gzip")
    if nested:
        data = b"--in" + headers.encode() + b"\r\n\r\n" + data + b"\r\n--in--"
        headers = "\r\nContent-Type: multipart/mixed; boundary=in"
    return _multipart_body({"f": (headers, data)})


async def _aiohttp_hands_attack(request: web.Request, body: bytes) -> bool:
    seen: list[str] = []
    _restore_request_body(request, body)
    with contextlib.suppress(Exception):
        seen.extend(
            v if isinstance(v, str) else bytes(v).decode(errors="replace") for v in (await request.post()).values()
        )

    async def walk(reader: MultipartReader) -> None:
        while (part := await reader.next()) is not None:
            if isinstance(part, MultipartReader):
                await walk(part)
                continue
            raw = await part.read(decode=False)
            for charset in (part.get_charset("utf-8"), "utf-8"):
                with contextlib.suppress(Exception):
                    seen.append(bytes(part.decode(raw)).decode(charset, errors="replace"))

    _restore_request_body(request, body)
    with contextlib.suppress(Exception):
        await walk(await request.multipart())
    return any(_ATTACK in value for value in seen)


class Test_Aiohttp(_Test_Aiohttp_Base, utils.Contrib_TestClass_For_Threats):
    @pytest.mark.skip(reason="API endpoint discovery is not supported for aiohttp")
    def test_api_endpoint_discovery(self, interface, find_resource):
        pass

    def test_large_streamed_upload(self, interface):
        body, content_type = _multipart_body({"upload": b"x" * (2 * 1024**2)})
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.post("/stream_upload/", data=body, content_type=content_type)
            assert self.status(response) == 200, self.body(response)
            assert self.body(response) == str(len(body))

    @pytest.mark.parametrize(("value", "blocked"), [("safe_value", False), ("yqrweytqwreasldhkuqwgervflnmlnli", True)])
    def test_multipart_reader_after_body_analysis(self, interface, entry_span, get_entry_span_tag, value, blocked):
        body, content_type = _multipart_body({"field": value.encode(), "other": b"other_value"})
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/multipart/", data=body, content_type=content_type)
            if blocked:
                self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-003")
            else:
                assert self.status(response) == 200, self.body(response)
                assert json.loads(self.body(response)) == {"field": value, "other": "other_value"}

    @pytest.mark.parametrize(
        ("fields", "method"),
        [
            ({"f": ("\r\nContent-Transfer-Encoding: base64", base64.b64encode(_ATTACK.encode()))}, "POST"),
            (
                {
                    "f": (
                        "\r\nContent-Transfer-Encoding: quoted-printable",
                        "".join(f"={b:02X}" for b in _ATTACK.encode()).encode(),
                    )
                },
                "POST",
            ),
            ({"f": ("\r\nContent-Type: text/plain; charset=utf-7", _utf7(_ATTACK).encode())}, "POST"),
            ({"j": ("\r\nContent-Type: application/json", b"{bad"), "f": _ATTACK.encode()}, "POST"),
            ({"b": ("\r\nContent-Transfer-Encoding: base64", b"abc"), "f": _ATTACK.encode()}, "POST"),
            ({"i": ("\r\nContent-Type: text/plain; charset=idna", b"x"), "f": _ATTACK.encode()}, "POST"),
            ({"j": ("\r\nContent-Type: application/json", b"[" * 100000), "f": _ATTACK.encode()}, "POST"),
            ({"q": ("; filename=\"x\"; filename*=UTF-8''", _ATTACK.encode())}, "POST"),
            (
                {
                    "f": (
                        "\r\nContent-Transfer-Encoding: base64\r\nContent-Type: text/plain; charset=idna",
                        base64.b64encode(_ATTACK.encode()),
                    )
                },
                "POST",
            ),
            (
                {
                    "f": (
                        "\r\nContent-Transfer-Encoding: base64\r\nContent-Type: application/json",
                        base64.b64encode(_ATTACK.encode()),
                    )
                },
                "POST",
            ),
            (
                {"n": ("\r\nContent-Type: multipart/mixed; boundary=in", f"--in\r\n\r\n{_ATTACK}\r\n--in--".encode())},
                "POST",
            ),
            ({"f": _ATTACK.encode()}, "GET"),
            ({"f": ("\r\nContent-Length: 100000", _ATTACK.encode())}, "POST"),
            ({"a": ("; filename*0*=xx''%41", b"v"), "f": _ATTACK.encode()}, "POST"),
            ({"f": ("\r\nContent-Type: application/octet-stream; charset=utf-16", _ATTACK.encode())}, "POST"),
            ({"j": ("\r\nContent-Type: application/json; charset=utf-16", f'{{"a": "{_ATTACK}"}}'.encode())}, "POST"),
            (
                {
                    "f": b"x\n--52d1fb4eb9c021e53ac2846190e4ac72\n"
                    + b'Content-Disposition: form-data; name="g"; filename="x"\n\n'
                    + _ATTACK.encode()
                },
                "POST",
            ),
            ({"f": ("\r\nContent-Type: Text/Plain; charset=utf-16", _ATTACK.encode())}, "POST"),
            (
                {"j": ("\r\nContent-Type: application/json; charset=utf-7", f'{{"a": "{_utf7(_ATTACK)}"}}'.encode())},
                "POST",
            ),
            (
                {
                    "u": (
                        "\r\nContent-Type: application/x-www-form-urlencoded",
                        f"pad={'a' * 5000}&x={_ATTACK}".encode(),
                    )
                },
                "POST",
            ),
            (
                {
                    "j": (
                        "\r\nContent-Type: application/json; charset=utf-7",
                        json.dumps({"pad": "a" * 70000, "a": _utf7(_ATTACK)}).encode(),
                    )
                },
                "POST",
            ),
            (
                {
                    "u": (
                        "\r\nContent-Type: application/x-www-form-urlencoded; charset=utf-7",
                        f"x={_utf7(_ATTACK)}".encode(),
                    )
                },
                "POST",
            ),
            (
                {
                    "u": ("\r\nContent-Type: application/x-www-form-urlencoded; charset=hex", b"x=1"),
                    "f": _ATTACK.encode(),
                },
                "POST",
            ),
            ({"c": ("\r\nContent-Type: text/plain; charset=\xff", b"x"), "f": _ATTACK.encode()}, "POST"),
            ({"f": ("\r\nContent-Type: text/plain; charset=hz", b"~\n" * 30000 + _ATTACK.encode())}, "POST"),
            (
                {
                    "n": (
                        "\r\nContent-Type: multipart/mixed; boundary=in",
                        b"--in\r\nContent-Encoding: gzip\r\n\r\n"
                        + gzip.compress(b"benign ")
                        + gzip.compress(_ATTACK.encode())
                        + b"\r\n--in--",
                    )
                },
                "POST",
            ),
            (
                {
                    "n": (
                        "\r\nContent-Type: multipart/mixed; boundary=in",
                        b"--in\r\nContent-Type: text/plain; charset=hz\r\nContent-Encoding: gzip\r\n\r\n"
                        + gzip.compress(b"~\n" * 40000 + _ATTACK.encode())
                        + b"\r\n--in--",
                    )
                },
                "POST",
            ),
            ({"f": ("\r\nContent-Type: text/plain; charset=utf.16be", _ATTACK.encode("utf-16-be"))}, "POST"),
            ({"j": ("\r\nContent-Type: application/json", f'{{"k": "v"}}-{_ATTACK}'.encode())}, "POST"),
            (
                {
                    "n": (
                        "\r\nContent-Type: multipart/mixed; boundary=in",
                        b"--in\r\nContent-Encoding: gzip\r\n\r\n" + gzip.compress(_ATTACK.encode()) + b"junk\r\n--in--",
                    )
                },
                "POST",
            ),
            ({"f": ("\r\nContent-Type: text/plain; charset=utf\u00e916\u00e9be", _ATTACK.encode("utf-16-be"))}, "POST"),
            (
                {
                    "n": (
                        "\r\nContent-Type: multipart/mixed; boundary=in",
                        b"--in\r\nContent-Encoding: deflate\r\n\r\n"
                        + zlib.compress(b"benign ")[2:-4]
                        + zlib.compress(_ATTACK.encode())[2:-4]
                        + b"\r\n--in--",
                    )
                },
                "POST",
            ),
            (
                {
                    "n": (
                        "\r\nContent-Type: multipart/mixed; boundary=in",
                        b"--in\r\nContent-Type: text/plain; charset=utf-16-be\r\nContent-Encoding: gzip\r\n\r\n"
                        + gzip.compress(_ATTACK.encode())
                        + b"\r\n--in--",
                    )
                },
                "POST",
            ),
        ],
        ids=[
            "base64",
            "quoted_printable",
            "utf7",
            "after_malformed_json",
            "after_bad_base64",
            "after_idna_charset",
            "after_deep_json",
            "empty_filename_star",
            "base64_then_idna_charset",
            "base64_then_invalid_json",
            "nested",
            "get",
            "lying_content_length",
            "after_bad_encoded_filename",
            "octet_stream_wrong_charset",
            "json_wrong_charset",
            "bare_lf_fake_boundary",
            "mixed_case_text_type",
            "json_part_declared_utf7",
            "padded_urlencoded_part",
            "large_json_part_declared_utf7",
            "urlencoded_part_declared_utf7",
            "after_bytes_codec_charset",
            "after_undecodable_charset_name",
            "after_zero_width_escapes",
            "nested_two_member_gzip",
            "nested_compressed_zero_width_escapes",
            "dotted_charset_name",
            "json_part_with_trailing_text",
            "nested_gzip_with_trailing_bytes",
            "non_ascii_charset_spelling",
            "nested_two_deflate_members",
            "nested_gzip_utf8_text_declared_utf16",
        ],
    )
    def test_multipart_parts_decoded_as_aiohttp(self, interface, entry_span, get_entry_span_tag, fields, method):
        body, content_type = _multipart_body(fields)
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client._run(method, "/asm/", data=body, content_type=content_type)
            self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-003")

    def test_multipart_fields_sent_to_waf(self, interface):
        body, content_type = _multipart_body(
            {
                "text": b"ok",
                "json": ("\r\nContent-Type: application/json", b'{"a": "b"}'),
                "upload": ('; filename="f.txt"', _ATTACK.encode()),
                "x": ("; name*=UTF-8''renamed", b"v"),
                "u": ("\r\nContent-Type: application/json", '{"c": "d"}'.encode("utf-16")),
                "b": ("\r\nContent-Transfer-Encoding: base64", base64.b64encode(b"hi")),
            }
        )
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/stream_upload/", data=body, content_type=content_type)
            assert self.status(response) == 200, self.body(response)
            assert self.body(response) == str(len(body))
            assert utils._addresses_store, "no waf addresses stored"
            assert utils._addresses_store[0]["http.request.body"] == {
                "text": "ok",
                "json": {"a": "b"},
                "renamed": "v",
                "u": {"c": "d"},
                "b": ["hi", "aGk="],
            }

    def test_multipart_part_limit(self, interface):
        body, content_type = _multipart_body({f"f{i}": b"v" for i in range(1500)})
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/stream_upload/", data=body, content_type=content_type)
            assert self.status(response) == 200, self.body(response)
            assert self.body(response) == str(len(body))
            assert utils._addresses_store, "no waf addresses stored"
            assert len(utils._addresses_store[0]["http.request.body"]) == 1000

    @pytest.mark.parametrize("case", ["alone", "after_large_part", "after_large_json_parts", "padded_json"])
    def test_multipart_gzip_part_follows_aiohttp(self, interface, entry_span, get_entry_span_tag, case):
        json_gzip = "\r\nContent-Type: application/json\r\nContent-Encoding: gzip"
        fields: dict[str, Union[bytes, tuple[str, bytes]]] = {}
        if case == "after_large_part":
            fields["big"] = ("\r\nContent-Encoding: gzip", gzip.compress(b"a" * 2 * 1024**2))
        if case == "after_large_json_parts":
            for i in range(64):
                fields[f"j{i}"] = (json_gzip, gzip.compress(b'"' + b"a" * 64 * 1024 + b'"'))
        if case == "padded_json":
            fields["f"] = (json_gzip, gzip.compress(json.dumps({"pad": "a" * 70000, "x": _ATTACK}).encode()))
        else:
            fields["f"] = ("\r\nContent-Encoding: gzip", gzip.compress(_ATTACK.encode()))
        body, content_type = _multipart_body(fields)
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/stream_upload/", data=body, content_type=content_type)
            if AIOHTTP_VERSION >= (3, 9, 4):
                assert self.status(response) == 200, self.body(response)
                assert self.body(response) == str(len(body))
            else:
                self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-003")

    def test_multipart_parts_past_max_depth_are_drained(self, interface, entry_span, get_entry_span_tag):
        body, content_type = _multipart_body(
            {
                "n": ("\r\nContent-Type: multipart/mixed; boundary=in", f"--in\r\n\r\n{_ATTACK}\r\n--in--".encode()),
                "f": _ATTACK.encode(),
            }
        )
        with (
            override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)),
            mock.patch.object(aiohttp_multipart, "_MAX_DEPTH", 0),
        ):
            self.update_tracer(interface)
            response = interface.client.post("/stream_upload/", data=body, content_type=content_type)
            self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-003")
            assert utils._addresses_store, "no waf addresses stored"
            assert "n" not in utils._addresses_store[0]["http.request.body"]

    def test_multipart_nested_reader_error_does_not_hang(self, interface):
        nested = b"--in\r\n\r\nx\r\n--inX\r\n\r\ny"
        body, content_type = _multipart_body(
            {
                "n": ("\r\nContent-Type: multipart/mixed; boundary=in", nested),
                "f": ("\r\nContent-Length: 100000", b"x"),
            }
        )
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/stream_upload/", data=body, content_type=content_type)
            assert self.status(response) == 200, self.body(response)

    def test_multipart_slow_charset_is_bounded(self, interface):
        body, content_type = _multipart_body({"f": ("\r\nContent-Type: text/plain; charset=punycode", b"a" * 900_000)})
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/stream_upload/", data=body, content_type=content_type)
            assert self.status(response) == 200, self.body(response)
            assert utils._addresses_store[0]["http.request.body"] == {"f": "a" * aiohttp_utils._MAX_STORED_LENGTH}

    @pytest.mark.parametrize("charset", ["utf-7", "latin-1"])
    def test_large_json_body_declared_charset(self, interface, entry_span, get_entry_span_tag, charset):
        body = json.dumps({"pad": "\xe9" * 70000, "a": _ATTACK}).encode("latin-1")
        if charset == "utf-7":
            body = json.dumps({"pad": "a" * 70000, "a": _utf7(_ATTACK)}).encode()
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/asm/", data=body, content_type=f"application/json; charset={charset}")
            self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-003")

    @pytest.mark.parametrize(
        ("content_type", "encode"),
        [
            ("application/json; charset=punycode", lambda text: text.encode("punycode")),
            ("application/json; charset*=utf-8''utf-8", lambda text: text.encode()),
            ("application/json; charset=idna", lambda text: _ATTACK.encode()),
            ("application/json", lambda text: f'{{"k": "v"}}-{_ATTACK}'.encode()),
        ],
        ids=["punycode", "rfc2231_charset", "invalid_json_idna", "trailing_text"],
    )
    def test_json_body_unusual_charset(self, interface, entry_span, get_entry_span_tag, content_type, encode):
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post(
                "/asm/", data=encode(json.dumps({"a": _ATTACK})), content_type=content_type
            )
            self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-003")

    def test_large_punycode_json_body(self, interface, entry_span, get_entry_span_tag):
        body = json.dumps({"pad": "a" * 5000, "a": _ATTACK}).encode("punycode")
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/asm/", data=body, content_type="application/json; charset=punycode")
            self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-003")

    @pytest.mark.parametrize(
        ("content_type", "payload"),
        [("application/json", b"[" + b"[]," * 5460 + b"[]]"), ("application/x-www-form-urlencoded", b"a=&" * 5460)],
    )
    def test_multipart_parsing_budget(self, interface, content_type, payload):
        part = gzip.compress(payload)
        inner = b"".join(
            f"--in\r\nContent-Type: {content_type}\r\nContent-Encoding: gzip\r\n\r\n".encode() + part + b"\r\n"
            for _ in range(200)
        )
        body, content_type = _multipart_body(
            {"n": ("\r\nContent-Type: multipart/mixed; boundary=in", inner + b"--in--")}
        )
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/stream_upload/", data=body, content_type=content_type)
            assert self.status(response) == 200, self.body(response)
            assert utils._addresses_store, "no waf addresses stored"
            parts = utils._addresses_store[0]["http.request.body"]["n"][""]
            assert 0 < sum(isinstance(value, str) for value in parts) < len(parts)

    def test_multipart_slow_charset_across_parts_is_bounded(self, interface):
        part = ("\r\nContent-Type: text/plain; charset=punycode", b"a" * 4000)
        body, content_type = _multipart_body({f"p{i}": part for i in range(15)})
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/stream_upload/", data=body, content_type=content_type)
            assert self.status(response) == 200, self.body(response)
            fields = utils._addresses_store[0]["http.request.body"]
            assert sum(isinstance(value, list) for value in fields.values()) == 4

    def test_form_body_non_utf8_charset_is_linear(self):
        body = b"&".join(b"k=v\xe9" + str(i).encode() for i in range(40000))
        start = time.monotonic()
        form = aiohttp_utils.parse_form(body, "latin-1")
        assert time.monotonic() - start < 3
        assert len(form["k"]) == 80000

    def test_multipart_waf_sees_what_aiohttp_hands_the_application(self):
        async def run() -> tuple[int, list[tuple[Any, ...]]]:
            handed, misses = 0, []
            for case in itertools.product(
                (
                    None,
                    "latin-1",
                    "utf-16",
                    "utf-7",
                    "punycode",
                    "UTF.16BE",
                    "ebcdic\u212acp\u212aus",
                    "cp037" + "-" * 60,
                ),
                ("text/plain", "application/json", "application/octet-stream", "application/x-www-form-urlencoded"),
                ("", "base64", "quoted-printable"),
                ("", "gzip", "gzip+junk", "two-gzip", "two-deflate"),
                (False, True),
            ):
                body, content_type = _differential_body(*case)
                request = make_mocked_request("POST", "/", headers=CIMultiDict({"Content-Type": content_type}))
                fields = await aiohttp_multipart.multipart_fields(request, body)
                if await _aiohttp_hands_attack(request, body):
                    handed += 1
                    if _ATTACK not in json.dumps(fields, default=str, ensure_ascii=False):
                        misses.append(case)
            return handed, misses

        handed, misses = asyncio.run(run())
        assert handed and not misses, misses

    def test_form_body_with_unusable_charset(self, interface, entry_span, get_entry_span_tag):
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post(
                "/asm/", data=f"attack={_ATTACK}", content_type="application/x-www-form-urlencoded; charset=idna"
            )
            self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-003")

    def test_x_url_encoded_body(self, interface, entry_span, get_entry_span_tag):
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post(
                "/asm/", data="attack=yqrweytqwreasldhkuqwgervflnmlnli", content_type="application/x-url-encoded"
            )
            self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-003")

    @pytest.mark.parametrize("encoding", ["json", "form"])
    def test_request_body_charset(self, interface, entry_span, get_entry_span_tag, encoding):
        attack = "yqrweytqwreasldhkuqwgervflnmlnli"
        utf7_attack = _utf7(attack)
        if encoding == "json":
            body, content_type = json.dumps({"attack": utf7_attack}), "application/json; charset=utf-7"
        else:
            body = "attack=" + urllib.parse.quote(utf7_attack, safe="")
            content_type = "application/x-www-form-urlencoded; charset=utf-7"
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.post("/asm/", data=body, content_type=content_type)
            self.assert_blocked(response, entry_span, get_entry_span_tag, "tst-037-003")

    def test_block_after_response_started(self, interface, entry_span, get_entry_span_tag):
        with override_global_config(
            dict(_asm_enabled=True, _ep_enabled=True, _asm_static_rule_file=rules.RULES_EXPLOIT_PREVENTION_BLOCKING)
        ):
            self.update_tracer(interface)
            with pytest.raises(ClientError):
                interface.client.get("/stream_rasp/?filename=/etc/passwd")
            interface.client.settle()
            self.check_single_rule_triggered("rasp-930-100", entry_span)
            assert get_entry_span_tag(http.STATUS_CODE) == "200"

    def test_streamed_response_is_not_replaced(self, interface, entry_span):
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_SRB)):
            self.update_tracer(interface)
            response = interface.client.get("/stream_headers/?headers=header_name%3DMagicKey_Al4h7iCFep9s1")
            assert self.status(response) == 200
            assert self.body(response) == "0123456789"
            interface.client.settle()
            assert get_triggers(entry_span()) is None

    def test_block_request(self, interface, get_entry_span_tag):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/block_request/")
            assert self.status(response) == 403, self.body(response)
            assert json.loads(self.body(response))["errors"][0]["title"] == "You've been blocked"
            assert get_entry_span_tag(http.STATUS_CODE) == "403"

    @pytest.mark.parametrize(("user_id", "blocked"), [("123456", True), ("111111", False)])
    def test_set_user_blocking(self, interface, entry_span, get_entry_span_tag, user_id, blocked):
        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self.update_tracer(interface)
            response = interface.client.get(f"/set_user/?user_id={user_id}")
            if blocked:
                self.assert_blocked(response, entry_span, get_entry_span_tag, "blk-001-002")
            else:
                assert self.status(response) == 200
                assert self.body(response) == "not blocked"

    def test_block_response_fallback(self, interface, get_entry_span_tag):
        from ddtrace.appsec._contrib.aiohttp import _on_block

        with override_global_config(dict(_asm_enabled=True, _asm_static_rule_file=rules.RULES_GOOD_PATH)):
            self.update_tracer(interface)
            core.reset_listeners("aiohttp.request.block", _on_block)
            try:
                response = interface.client.get("/", headers={"X-Real-Ip": rules._IP.BLOCKED})
            finally:
                core.on("aiohttp.request.block", _on_block)
            assert self.status(response) == 403
            assert json.loads(self.body(response))["errors"][0]["title"] == "You've been blocked"
            assert get_entry_span_tag(http.STATUS_CODE) == "403"

    def test_repeated_request_headers(self, interface):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers=[("X-Repeated", "first"), ("X-Repeated", "second")])
            assert self.status(response) == 200
            assert utils._addresses_store, "no waf addresses stored"
            assert utils._addresses_store[0]["http.request.headers"]["x-repeated"] == "first, second"

    def test_truncated_request_body(self, interface, entry_span, get_entry_span_tag):
        loop = interface.client.loop
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            runner = web.AppRunner(get_app())
            loop.run_until_complete(runner.setup())
            site = web.TCPSite(runner, "127.0.0.1", 0)
            loop.run_until_complete(site.start())
            port = site._server.sockets[0].getsockname()[1]

            async def send_truncated_body():
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.write(
                    b"POST / HTTP/1.1\r\nHost: localhost\r\nContent-Type: application/json\r\n"
                    b'Content-Length: 100\r\n\r\n{"a":'
                )
                await writer.drain()
                writer.close()

            try:
                loop.run_until_complete(send_truncated_body())
                interface.client.settle(until=lambda: entry_span() is not None)
            finally:
                loop.run_until_complete(runner.cleanup())
            assert get_entry_span_tag(http.STATUS_CODE) == "200"
            assert not entry_span().error

    @pytest.mark.parametrize(
        ("cookie_headers", "expected"),
        [
            (("x-attack=var_dump ()",), {"x-attack": "var_dump ()"}),
            (('quoted="a b\\073c"; plain=1',), {"quoted": "a b;c", "plain": "1"}),
            (("first=1", "second=2"), {"first": "1", "second": "2"}),
            (("session=' OR 1=1 --", "session=x"), {"session": ["' OR 1=1 --", "x"]}),
            (("a=1; a=2",), {"a": ["1", "2"]}),
            (('session="safe;\\152dfoSDGFkivRG_234"; other=1',), {"session": "safe;jdfoSDGFkivRG_234", "other": "1"}),
            (('a=x"y; b=z',), {"a": 'x"y', "b": "z"}),
        ],
    )
    def test_request_cookies_raw_header(self, interface, cookie_headers, expected):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/", headers=[("Cookie", value) for value in cookie_headers])
            assert self.status(response) == 200
            assert utils._addresses_store, "no waf addresses stored"
            assert utils._addresses_store[0]["http.request.cookies"] == expected

    def test_unhandled_error_response_status(self, interface):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/error/")
            assert self.status(response) == 500
            assert utils._addresses_store, "no waf addresses stored"
            assert utils._addresses_store[0].get("http.response.status") == "500"

    @pytest.mark.parametrize(
        ("rule_file", "action_level", "status_expected"),
        [
            (rules.RULES_EXPLOIT_PREVENTION, 1, 200),
            (rules.RULES_EXPLOIT_PREVENTION_BLOCKING, 2, 403),
            (rules.RULES_EXPLOIT_PREVENTION_REDIRECTING, 2, 301),
            (rules.RULES_EXPLOIT_PREVENTION_DISABLED, 0, 200),
        ],
    )
    def test_exploit_prevention_aiohttp_client(
        self,
        interface,
        entry_span,
        get_entry_span_tag,
        get_entry_span_metric,
        rule_file,
        action_level,
        status_expected,
    ):
        utils.Contrib_TestClass_For_Threats.test_exploit_prevention(
            self,
            interface,
            entry_span,
            get_entry_span_tag,
            get_entry_span_metric,
            True,
            True,
            "ssrf",
            {"url_aiohttp_1": "127.0.0.1:1", "url_aiohttp_2": "127.0.0.1:2"},
            "rasp-934-100",
            ("_request",),
            rule_file,
            action_level,
            status_expected,
        )

    def test_api10_aiohttp_client(self, interface, get_tag):
        utils.Contrib_TestClass_For_Threats.test_api10(self, "aiohttp", interface, get_tag)

    @pytest.mark.parametrize(
        ("route", "data", "tag"),
        [
            ("request-headers", None, "TAG_API10_REQ_HEADERS"),
            ("request-body", {"payload": "api10-request-body"}, "TAG_API10_REQ_BODY"),
            ("response-headers", None, "TAG_API10_RESP_HEADERS"),
            ("response-body", None, "TAG_API10_RESP_BODY"),
            ("response-status", None, "TAG_API10_RESP_STATUS"),
        ],
    )
    def test_api10_addresses_aiohttp_client(self, route, data, tag, interface, api10_server, test_spans, get_tag):
        utils.Contrib_TestClass_For_Threats.test_api10_addresses(
            self, "_aiohttp", route, data, tag, interface, api10_server, test_spans, get_tag
        )

    def test_api10_addresses_redirects_aiohttp_client(self, interface, api10_server, test_spans, entry_span):
        utils.Contrib_TestClass_For_Threats.test_api10_addresses_redirects(
            self, "_aiohttp", interface, api10_server, test_spans, entry_span
        )

    def _waf_calls(self):
        return mock.patch.object(
            _common_module_patches, "call_waf_callback", wraps=_common_module_patches.call_waf_callback
        )

    def test_api10_streamed_response_body_aiohttp_client(self, interface, api10_server):
        with (
            override_global_config(
                dict(
                    _asm_enabled=True,
                    _api_security_enabled=True,
                    _ep_enabled=True,
                    _asm_static_rule_file=rules.RULES_EXPLOIT_PREVENTION,
                    _dr_sample_rate=1.0,
                )
            ),
            self._waf_calls() as waf_calls,
        ):
            self.update_tracer(interface)
            response = interface.client.get(f"/redirect_aiohttp/response-body/{api10_server.port()}?stream=1")
            assert self.status(response) == 200
            assert json.loads(self.body(response))["payload"] == API10_RESPONSE_BODY.decode()
            addresses = [call.args[0] for call in waf_calls.call_args_list]
            assert any(a.get("DOWN_RES_STATUS") == "200" for a in addresses), addresses
            assert not any("DOWN_RES_BODY" in a for a in addresses), addresses

    def test_api10_proxy_connect_response_is_ignored(self, interface):
        async def proxy(reader, writer):
            await reader.readuntil(b"\r\n\r\n")
            writer.write(
                b"HTTP/1.1 407 Proxy Authentication Required\r\n"
                b"Content-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
            )
            await writer.drain()
            writer.close()

        server = interface.client.loop.run_until_complete(asyncio.start_server(proxy, "127.0.0.1", 0))
        try:
            with (
                override_global_config(
                    dict(
                        _asm_enabled=True,
                        _api_security_enabled=True,
                        _ep_enabled=True,
                        _asm_static_rule_file=rules.RULES_EXPLOIT_PREVENTION,
                        _dr_sample_rate=1.0,
                    )
                ),
                self._waf_calls() as waf_calls,
            ):
                self.update_tracer(interface)
                port = server.sockets[0].getsockname()[1]
                response = interface.client.get(f"/aiohttp_proxy/{port}")
                assert self.status(response) == 200
                assert self.body(response) == "error ClientHttpProxyError"
                addresses = [call.args[0] for call in waf_calls.call_args_list]
                assert any(a.get(EXPLOIT_PREVENTION.ADDRESS.SSRF) == "https://www.datadoghq.com/" for a in addresses), (
                    addresses
                )
                assert not any("DOWN_RES_STATUS" in a for a in addresses), addresses
        finally:
            server.close()
            interface.client.loop.run_until_complete(server.wait_closed())

    def _client_cases(self, interface, api10_server, *cases):
        calls = []
        waf = _common_module_patches.call_waf_callback

        def record(addresses, *args, **kwargs):
            calls.append((addresses, core.find_item(_RASP_SUBCONTEXT)))
            return waf(addresses, *args, **kwargs)

        with (
            override_global_config(dict(_asm_enabled=True, _ep_enabled=True, _dr_sample_rate=1.0)),
            mock.patch.object(_common_module_patches, "call_waf_callback", side_effect=record),
        ):
            self.update_tracer(interface)
            try:
                for case in cases:
                    response = interface.client.get(f"/aiohttp_client/{case}/{api10_server.port()}")
                    assert self.status(response) == 200, self.body(response)
            finally:
                while _STORED_RESPONSES:
                    interface.client.loop.run_until_complete(_STORED_RESPONSES.pop()[0].close())
        return calls

    @pytest.mark.skipif(AIOHTTP_VERSION < (3, 12), reason="client middlewares need aiohttp 3.12")
    def test_aiohttp_client_failover_hop_is_analyzed(self, interface, api10_server):
        calls = self._client_cases(interface, api10_server, "failover")
        assert [a[EXPLOIT_PREVENTION.ADDRESS.SSRF] for a, _ in calls if EXPLOIT_PREVENTION.ADDRESS.SSRF in a] == [
            "http://127.0.0.1:1/",
            f"http://127.0.0.1:{api10_server.port()}/response-body",
        ]

    @pytest.mark.skipif(AIOHTTP_VERSION < (3, 12), reason="client middlewares need aiohttp 3.12")
    def test_aiohttp_client_nested_request_has_own_subcontext(self, interface, api10_server):
        calls = self._client_cases(interface, api10_server, "nested")
        holders = [holder for a, holder in calls if EXPLOIT_PREVENTION.ADDRESS.SSRF in a]
        assert len(holders) == 2 and holders[0] is not holders[1]

    def test_aiohttp_client_chunked_json_body_is_analyzed(self, interface, api10_server):
        calls = self._client_cases(interface, api10_server, "chunked")
        assert any("DOWN_RES_BODY" in a for a, _ in calls), calls

    def test_aiohttp_client_body_read_by_another_request_is_ignored(self, interface, api10_server):
        calls = self._client_cases(interface, api10_server, "store", "read")
        assert any("DOWN_RES_STATUS" in a for a, _ in calls), calls
        assert not any("DOWN_RES_BODY" in a for a, _ in calls), calls

    def test_aiohttp_client_traceback_has_no_appsec_frames(self):
        patch_common_modules()
        assert _SsrfAiohttpConnect.is_wrapped(aiohttp.connector.BaseConnector.connect)

        async def call():
            async with ClientSession() as session:
                await session.get("http://127.0.0.1:1/")

        with pytest.raises(ClientError) as raised:
            asyncio.run(call())
        frames = traceback.extract_tb(raised.value.__traceback__)
        appsec_frames = [frame.filename for frame in frames if "/ddtrace/appsec/" in frame.filename]
        assert not appsec_frames, appsec_frames


class Test_Aiohttp_RC(_Test_Aiohttp_Base, utils.Contrib_TestClass_For_Threats_RC):
    pass
