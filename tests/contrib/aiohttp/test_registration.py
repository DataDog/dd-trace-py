import os

import pytest


@pytest.mark.parametrize("startup", ["manual", "auto", "ddtrace-run"])
def test_tracing_initialization_registers_request_subscriber(
    run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess, startup
):
    code = """
import asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from ddtrace.contrib.aiohttp import trace_app
from ddtrace.internal import core
from ddtrace.trace import tracer

spans = []


def on_started(ctx):
    # Observe before middleware can create a fallback span.
    span = ctx.get_item("_inner_span")
    assert span is not None
    assert span.name == "aiohttp.request"
    spans.append(span)


async def handler(request):
    assert tracer.current_span() is spans[0]
    return web.Response(text="ok")


async def main():
    app = web.Application()
    app.router.add_get("/", handler)
    trace_app(app)
    core.on("context.started.web.request", on_started)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/")
        assert response.status == 200
        assert await response.text() == "ok"
    assert len(spans) == 1
    assert spans[0].finished
    assert spans[0].get_tag("http.status_code") == "200"


asyncio.run(main())
"""
    env = os.environ.copy()
    env.update(
        DD_TRACE_ENABLED="true",
        DD_APM_TRACING_ENABLED="true",
        DD_APPSEC_ENABLED="false",
        DD_IAST_ENABLED="false",
    )
    if startup == "auto":
        code = "import ddtrace.auto\n" + code
    run = ddtrace_run_python_code_in_subprocess if startup == "ddtrace-run" else run_python_code_in_subprocess
    out, err, status, _ = run(code, env=env)
    assert status == 0, (err.decode(), out.decode())
