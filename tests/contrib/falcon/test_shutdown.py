import pytest


@pytest.mark.subprocess()
def test_requests_after_tracing_product_stop() -> None:
    from unittest.mock import patch

    import falcon
    from falcon.testing import TestClient

    from ddtrace._trace import product
    from ddtrace.contrib.internal.falcon.middleware import TraceMiddleware
    from ddtrace.internal import core
    from ddtrace.trace import tracer

    contexts = []
    statuses = []

    def started(ctx):
        assert ctx.get_item("_inner_span") is None
        contexts.append(ctx)

    def ended(ctx, exc_info):
        statuses.append(ctx.event.response_status_code)

    class Resource:
        def on_get(self, req, resp):
            if req.path == "/error":
                raise falcon.HTTPInternalServerError()
            resp.status = falcon.HTTP_200

    app = falcon.App(middleware=[TraceMiddleware()])
    app.add_route("/ok", Resource())
    app.add_route("/error", Resource())
    client = TestClient(app)
    product.stop()
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")
    core.on("context.started.web.request", started)
    core.on("context.ended.web.request", ended)
    with patch.object(tracer, "start_span", wraps=tracer.start_span) as start_span:
        for _ in range(2):
            for path, status in (("/ok", 200), ("/error", 500), ("/missing", 404)):
                assert client.simulate_get(path).status_code == status
    start_span.assert_not_called()
    assert len(contexts) == len(statuses) == 6
    assert statuses == [200, 500, 404] * 2
    assert all(ctx._end_event_dispatched and ctx._event is None for ctx in contexts)
    assert not tracer._span_aggregator._traces
    assert tracer.current_span() is None
