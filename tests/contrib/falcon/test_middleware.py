from unittest import mock

from falcon import testing
import pytest

from ddtrace.contrib.internal.falcon.middleware import TraceMiddleware
from ddtrace.contrib.internal.falcon.patch import FALCON_VERSION
from tests.utils import TracerTestCase

from .app.app import get_app
from .test_suite import FalconTestCase


class MiddlewareTestCase(TracerTestCase, testing.TestCase, FalconTestCase):
    """Executes tests using the manual instrumentation so a middleware
    is explicitly added.
    """

    def setUp(self):
        super().setUp()

        # build a test app with a dummy tracer
        self._service = "falcon"
        self.api = get_app(tracer=self.tracer)
        if FALCON_VERSION >= (2, 0, 0):
            self.client = testing.TestClient(self.api)
        else:
            self.client = self

    def test_request_lifecycle_without_spans(self):
        from ddtrace._trace.subscribers.web_framework import WebFrameworkRequestSubscriber
        from ddtrace.contrib.internal import trace_utils
        from ddtrace.internal import core

        WebFrameworkRequestSubscriber.register()
        started = []
        completed = []

        def beginning(ctx):
            started.append(ctx)

        def ended(ctx, exc_info):
            completed.append((ctx, ctx.event.response_status_code))

        core.on("context.started.web.request", beginning)
        core.on("context.ended.web.request", ended)
        try:
            with (
                mock.patch.object(self.tracer, "enabled", False),
                mock.patch.object(trace_utils, "standalone_config", mock.Mock(apm_opt_out=False)),
                mock.patch.object(self.tracer, "start_span", wraps=self.tracer.start_span) as start_span,
            ):
                for path, status in [("/200", 200), ("/missing", 404), ("/500", 500)]:
                    self.make_test_call(path, expected_status_code=status)
                try:
                    self.make_test_call("/exception")
                except Exception:
                    if FALCON_VERSION >= (3, 0, 0):
                        raise
            start_span.assert_not_called()
            assert len(started) == 4
            assert completed == list(zip(started, [200, 404, 500, 500]))
            assert all(ctx._event is None for ctx in started)
        finally:
            core.reset_listeners("context.started.web.request", beginning)
            core.reset_listeners("context.ended.web.request", ended)


@pytest.mark.parametrize(
    "root_path,uri_template,want_route",
    [
        # Regression: ``route = req.root_path or "" + req.uri_template`` was
        # parsed as ``root_path or ("" + uri_template)`` so a non-empty
        # ``root_path`` short-circuited and the route lost the template
        # entirely (the span's ``http.route`` ended up being just the
        # WSGI mount prefix).
        ("/api", "/users/{id}", "/api/users/{id}"),
        ("/api/", "/users/{id}", "/api//users/{id}"),
        # Root-mounted app: behaviour unchanged.
        ("", "/users/{id}", "/users/{id}"),
        (None, "/users/{id}", "/users/{id}"),
        # Custom router that returns no uri_template: must not raise TypeError.
        ("/prefix", None, "/prefix"),
        ("", None, ""),
        (None, None, ""),
    ],
)
def test_process_response_route_includes_root_path(root_path, uri_template, want_route):
    middleware = TraceMiddleware()
    span = mock.MagicMock()
    ctx = mock.MagicMock()
    ctx.event = mock.MagicMock()

    req = mock.MagicMock()
    req.root_path = root_path
    req.uri_template = uri_template
    req.method = "GET"
    req.env = {
        middleware._request_context_key: ctx,
    }

    resp = mock.MagicMock()
    resp.status = "200 OK"
    resp._headers = {}

    ctx.get_item.return_value = span
    middleware.process_response(req, resp, mock.MagicMock(), req_succeeded=True)

    assert ctx.event.request_route == want_route
    assert ctx.event.response_status_code == 200
    assert ctx.event.response_headers == {}
    ctx.dispatch_ended_event.assert_called_once_with()
