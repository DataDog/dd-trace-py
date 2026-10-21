import asyncio
import base64
from http.cookies import SimpleCookie
import json
import time
import traceback
from typing import Any
from typing import Optional
from unittest import mock
import urllib.parse

from aiohttp import ClientError
from aiohttp import ClientSession
from aiohttp import web
import aiohttp.connector
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict
import pytest
from yarl import URL

from ddtrace.appsec import _common_module_patches
from ddtrace.appsec._asm_request_context import _RASP_SUBCONTEXT
from ddtrace.appsec._common_module_patches import _SsrfAiohttpConnect
from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._utils import get_triggers
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.packages import get_version_for_package
from tests.appsec import rules
from tests.appsec.contrib_appsec import utils
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


def _multipart_body(fields: dict[str, bytes]) -> tuple[bytes, str]:
    boundary = "52d1fb4eb9c021e53ac2846190e4ac72"
    body = b"".join(
        f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'.encode() + value + b"\r\n"
        for name, value in fields.items()
    )
    return body + f"--{boundary}--\r\n".encode(), f"multipart/form-data; boundary={boundary}"


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
        utf7_attack = "+" + base64.b64encode(attack.encode("utf-16-be")).decode().rstrip("=") + "-"
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
            for case in cases:
                assert self.status(interface.client.get(f"/aiohttp_client/{case}/{api10_server.port()}")) == 200
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
