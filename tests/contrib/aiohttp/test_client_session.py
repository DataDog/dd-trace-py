from aiohttp import ClientSession
from aiohttp.test_utils import TestServer

from ddtrace.contrib.aiohttp.trace_config import DataDog


async def test_client_session_header_tracing(patched_app_tracer, loop):
    app, _ = patched_app_tracer
    server = TestServer(app)

    # TODO: move server fixture to conftest

    async with ClientSession(trace_configs=[DataDog()]) as session:
        resp = await session.get(server.make_url("/echo_request_headers"))
        assert resp.status == 200

    # the server would have received the headers in this case
    assert "request-x-datadog-trace-id" in resp.headers
    assert "request-x-datadog-parent-id" in resp.headers

    await server.close()
