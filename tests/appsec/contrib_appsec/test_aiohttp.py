import asyncio
from http.cookies import SimpleCookie
from typing import Any
from typing import Optional
import urllib.parse

from aiohttp import ClientSession
from aiohttp.test_utils import TestServer
import pytest
from yarl import URL

from ddtrace.internal.packages import get_version_for_package
from tests.appsec.contrib_appsec import utils
from tests.appsec.contrib_appsec.aiohttp_app.app import get_app
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
        headers = dict(kwargs.pop("headers", None) or {})
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


class Test_Aiohttp(_Test_Aiohttp_Base, utils.Contrib_TestClass_For_Threats):
    @pytest.mark.skip(reason="API endpoint discovery is not supported for aiohttp")
    def test_api_endpoint_discovery(self, interface, find_resource):
        pass


class Test_Aiohttp_RC(_Test_Aiohttp_Base, utils.Contrib_TestClass_For_Threats_RC):
    pass
