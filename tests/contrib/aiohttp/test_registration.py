import os

import pytest


@pytest.mark.parametrize("startup", ["manual", "auto", "ddtrace-run"])
@pytest.mark.parametrize("tracing_enabled", ["true", "false"])
def test_tracing_initialization_registers_request_subscriber(
    run_python_code_in_subprocess, ddtrace_run_python_code_in_subprocess, startup, tracing_enabled
):
    code = """
import asyncio
from unittest.mock import Mock
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer
from ddtrace.contrib.aiohttp import trace_app
from ddtrace.internal import core
from ddtrace.internal.settings._config import config
from ddtrace.trace import tracer

expect_tracing = config._tracing_enabled
assert tracer.enabled is expect_tracing
assert core.has_listeners("context.started.web.request")
assert core.has_listeners("context.ended.web.request")
if not expect_tracing:
    writer = Mock()
    tracer._span_aggregator.writer = writer
    start_span = Mock(wraps=tracer.start_span)
    tracer.start_span = start_span

spans = []
lifecycle = []


def on_started(ctx):
    # Observe before middleware can create a fallback span.
    lifecycle.append(("started", id(ctx)))
    span = ctx.get_item("_inner_span")
    if expect_tracing:
        assert span is not None
        assert span.name == "aiohttp.request"
        spans.append(span)
    else:
        assert span is None


def on_ended(ctx, exc_info):
    lifecycle.append(("ended", id(ctx), ctx.event.response_status_code))


async def handler(request):
    assert tracer.current_span() is (spans[0] if expect_tracing else None)
    return web.Response(text="ok")


async def main():
    app = web.Application()
    app.router.add_get("/", handler)
    trace_app(app)
    core.on("context.started.web.request", on_started)
    core.on("context.ended.web.request", on_ended)
    async with TestClient(TestServer(app)) as client:
        response = await client.get("/")
        assert response.status == 200
        assert await response.text() == "ok"
    assert len(lifecycle) == 2, lifecycle
    assert lifecycle[0][0] == "started", lifecycle
    assert lifecycle[1] == ("ended", lifecycle[0][1], 200), lifecycle
    if expect_tracing:
        assert len(spans) == 1
        assert spans[0].finished
        assert spans[0].get_tag("http.status_code") == "200"
    else:
        assert spans == []
        start_span.assert_not_called()
        writer.write.assert_not_called()
    assert tracer.current_span() is None


asyncio.run(main())
"""
    env = os.environ.copy()
    env.update(
        DD_TRACE_ENABLED=tracing_enabled,
        DD_APM_TRACING_ENABLED="true",
        DD_APPSEC_ENABLED="false",
        DD_IAST_ENABLED="false",
        DD_LLMOBS_ENABLED="false",
    )
    if startup == "auto":
        code = "import ddtrace.auto\n" + code
    run = ddtrace_run_python_code_in_subprocess if startup == "ddtrace-run" else run_python_code_in_subprocess
    out, err, status, _ = run(code, env=env)
    assert status == 0, (err.decode(), out.decode())
