"""Tests for the autopatched constructor, i.e. what ddtrace-run users get.

They run in a subprocess because patching cannot be undone, and an autopatched falcon.App
would add a second TraceMiddleware to the manually instrumented apps of the other tests.
"""

import pytest


def _falcon_asgi_importable():
    try:
        import falcon.asgi  # noqa: F401
    except ImportError:
        return False
    return True


# falcon.asgi in Falcon 3.0.x imports asyncio.coroutines.CoroWrapper, which Python 3.11+ removed.
@pytest.mark.skipif(not _falcon_asgi_importable(), reason="falcon.asgi cannot be imported on this Python version")
@pytest.mark.subprocess(ddtrace_run=True)
def test_asgi_app_starts_and_serves_requests():
    from falcon import testing
    import falcon.asgi

    calls = []

    class AsyncMiddleware:
        async def process_request(self, req, resp):
            calls.append(req.path)

    class Resource:
        async def on_get(self, req, resp):
            resp.text = "ok"

    # Falcon 4 forwards middleware positionally from falcon.asgi.App to falcon.App.__init__.
    for app in (falcon.asgi.App(), falcon.asgi.App(middleware=[AsyncMiddleware()])):
        app.add_route("/", Resource())
        result = testing.TestClient(app).simulate_get("/")
        assert result.status_code == 200, result.status

    assert calls == ["/"], calls


@pytest.mark.subprocess(ddtrace_run=True)
def test_wsgi_app_middleware_argument_forms():
    import warnings

    import falcon
    from falcon import testing

    from ddtrace.trace import tracer
    from tests.utils import DummyWriter

    tracer._span_aggregator.writer = DummyWriter()

    class Middleware:
        def __init__(self):
            self.calls = 0

        def process_request(self, req, resp):
            self.calls += 1

    class Resource:
        def on_get(self, req, resp):
            resp.text = "ok"

    def check(label, app, *user_middleware):
        app.add_route("/", Resource())
        result = testing.TestClient(app).simulate_get("/")
        assert result.status_code == 200, (label, result.status)
        assert [mw.calls for mw in user_middleware] == [1] * len(user_middleware), label
        # Exactly one request span: the app is instrumented, and only once.
        spans = tracer._span_aggregator.writer.pop()
        assert [span.name for span in spans] == ["falcon.request"], (label, spans)

    check("no middleware", falcon.App())
    check("middleware=None", falcon.App(middleware=None))

    mw = Middleware()
    check("single component", falcon.App(middleware=mw), mw)

    mw = Middleware()
    check("tuple", falcon.App(middleware=(mw,)), mw)

    mw = Middleware()
    check("positional", falcon.App(falcon.MEDIA_JSON, falcon.Request, falcon.Response, [mw]), mw)

    if hasattr(falcon, "API"):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            api = falcon.API()
        check("falcon.API", api)

    shared = [Middleware()]
    falcon.App(middleware=shared)
    falcon.App(middleware=shared)
    assert len(shared) == 1, shared
