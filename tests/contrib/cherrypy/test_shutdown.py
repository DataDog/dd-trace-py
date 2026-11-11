import pytest


@pytest.mark.subprocess()
def test_requests_after_tracing_product_stop() -> None:
    from io import BytesIO
    from typing import Any
    from unittest.mock import patch
    from wsgiref.util import setup_testing_defaults

    import cherrypy

    from ddtrace._trace import product
    from ddtrace.contrib.internal.cherrypy.patch import TraceMiddleware
    from ddtrace.internal import core
    from ddtrace.trace import tracer

    contexts = []
    statuses = []

    def started(ctx):
        assert ctx.get_item("_inner_span") is None
        contexts.append(ctx)

    def ended(ctx, exc_info):
        statuses.append(ctx.event.response_status_code)

    class App:
        @cherrypy.expose
        def index(self):
            return "ok"

        @cherrypy.expose
        def error(self):
            raise cherrypy.HTTPError(500)

    cherrypy.config.update({"log.screen": False})
    TraceMiddleware(cherrypy)
    app = cherrypy.Application(App(), config={"/": {"tools.tracer.on": True}})
    product.stop()
    assert not core.has_listeners("context.started.web.request")
    assert not core.has_listeners("context.ended.web.request")
    core.on("context.started.web.request", started)
    core.on("context.ended.web.request", ended)
    with patch.object(tracer, "start_span", wraps=tracer.start_span) as start_span:
        for _ in range(2):
            for path, status in (("/", 200), ("/error", 500), ("/missing", 404)):
                environ: dict[str, Any] = {}
                setup_testing_defaults(environ)
                environ.update(PATH_INFO=path, SERVER_PROTOCOL="HTTP/1.1", **{"wsgi.input": BytesIO()})
                responses = []
                result = app(environ, lambda status, headers, exc_info=None: responses.append(status))
                try:
                    list(result)
                finally:
                    result.close()
                assert int(responses[0].split()[0]) == status
    start_span.assert_not_called()
    assert len(contexts) == len(statuses) == 6
    assert statuses == [200, 500, 404] * 2
    assert all(ctx._end_event_dispatched and ctx._event is None for ctx in contexts)
    assert not tracer._span_aggregator._traces
    assert tracer.current_span() is None
