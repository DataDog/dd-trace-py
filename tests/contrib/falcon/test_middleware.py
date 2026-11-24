import os
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

    with mock.patch("ddtrace.contrib.internal.falcon.middleware.span_from_context", return_value=span):
        middleware.process_response(req, resp, mock.MagicMock(), req_succeeded=True)

    assert ctx.event.request_route == want_route
    assert ctx.event.response_status_code == 200
    assert ctx.event.response_headers == {}
    ctx.dispatch_ended_event.assert_called_once_with()


@pytest.mark.parametrize("startup", ["manual", "auto"])
def test_disabled_tracing_finishes_request_spans(run_python_code_in_subprocess, startup):
    code = """
import falcon
from falcon import testing
from ddtrace.contrib.internal.falcon.middleware import TraceMiddleware
from ddtrace.trace import tracer

spans = []
class Resource:
    def on_get(self, req, resp):
        span = tracer.current_span()
        assert span is not None
        assert span.name == "falcon.request", span.name
        spans.append(span)
        resp.text = "ok"

app = falcon.App(middleware=[TraceMiddleware()])
app.add_route("/", Resource())
client = testing.TestClient(app)
for _ in range(2):
    assert client.simulate_get("/").status_code == 200
    assert spans[-1].finished
    assert tracer.current_span() is None
assert len(spans) == 2
"""
    if startup == "auto":
        code = "import ddtrace.auto\n" + code
    env = os.environ.copy()
    env["DD_TRACE_ENABLED"] = "false"
    env["DD_APPSEC_ENABLED"] = "false"
    out, err, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, (err.decode(), out.decode())
