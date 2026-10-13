import asyncio
from http.cookies import SimpleCookie
import json
import time
from typing import Any
from typing import Optional
import urllib.parse

from aiohttp import ClientError
from aiohttp import ClientSession
from aiohttp import web
from aiohttp.test_utils import TestServer
from multidict import CIMultiDict
import pytest
from yarl import URL

from ddtrace.appsec._utils import get_triggers
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.packages import get_version_for_package
from tests.appsec import rules
from tests.appsec.contrib_appsec import utils
from tests.appsec.contrib_appsec.aiohttp_app.app import get_app
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
                core.on("aiohttp.request.block", _on_block, "aiohttp_block_response")
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

    def test_unhandled_error_response_status(self, interface):
        with override_global_config(dict(_asm_enabled=True)):
            self.update_tracer(interface)
            response = interface.client.get("/error/")
            assert self.status(response) == 500
            assert utils._addresses_store, "no waf addresses stored"
            assert utils._addresses_store[0].get("http.response.status") == "500"


class Test_Aiohttp_RC(_Test_Aiohttp_Base, utils.Contrib_TestClass_For_Threats_RC):
    pass
