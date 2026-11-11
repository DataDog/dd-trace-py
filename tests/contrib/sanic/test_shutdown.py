import pytest


@pytest.mark.subprocess(parametrize={"OTEL_SEMCONV_STABILITY_OPT_IN": ["", "http"]})
def test_requests_after_tracing_product_stop() -> None:
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import patch

    from ddtrace._trace import product
    from ddtrace.contrib.internal.sanic.patch import _create_sanic_request_context
    from ddtrace.contrib.internal.sanic.patch import _finish_request
    from ddtrace.contrib.internal.sanic.patch import sanic_http_lifecycle_exception
    from ddtrace.contrib.internal.sanic.patch import sanic_http_lifecycle_response
    from ddtrace.contrib.internal.sanic.patch import sanic_http_routing_after
    from ddtrace.internal import core
    from ddtrace.trace import tracer

    product.stop()
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")
    statuses = []
    core.on("context.ended.web.request", lambda ctx, exc_info: statuses.append(ctx.event.response_status_code))

    async def run():
        with patch.object(tracer, "start_span", wraps=tracer.start_span) as start_span:
            for error in (False, True):
                request = SimpleNamespace(
                    ctx=SimpleNamespace(),
                    headers={},
                    query_string="",
                    scheme="http",
                    host="localhost",
                    path="/users/1",
                    method="GET",
                    match_info={},
                )
                ctx = _create_sanic_request_context(request)
                assert ctx.get_item("_inner_span") is None
                route = SimpleNamespace(raw_path="users/<id>", regex=False, name="user")
                await sanic_http_routing_after(request, route, {}, lambda: None)
                if error:
                    # Older Sanic needs its response wrapper to finish an error context.
                    with patch("ddtrace.contrib.internal.sanic.patch.SANIC_VERSION", (21, 9, 0)):
                        await sanic_http_lifecycle_exception(request, RuntimeError("handler failed"))
                    assert request.ctx.__dd_span_call_finish
                await sanic_http_lifecycle_response(request, SimpleNamespace(status=500 if error else 200, headers={}))
                _finish_request(request)
                assert ctx._end_event_dispatched and ctx._event is None
        start_span.assert_not_called()
        assert statuses == [200, 500]
        assert not tracer._span_aggregator._traces
        assert tracer.current_span() is None

    asyncio.run(run())
