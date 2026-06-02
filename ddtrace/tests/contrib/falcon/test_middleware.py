from unittest import mock

from falcon import testing
import pytest

from ddtrace.contrib.internal.falcon.middleware import TraceMiddleware
from ddtrace.contrib.internal.falcon.patch import FALCON_VERSION
from tests.utils import TracerTestCase

from .app import get_app
from .test_suite import FalconTestCase


class MiddlewareTestCase(TracerTestCase, testing.TestCase, FalconTestCase):
    """Executes tests using the manual instrumentation so a middleware
    is explicitly added.
    """

    def setUp(self):
        super(MiddlewareTestCase, self).setUp()

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
    span = mock.MagicMock()
    req = mock.MagicMock()
    req.root_path = root_path
    req.uri_template = uri_template
    req.method = "GET"
    resp = mock.MagicMock()
    resp.status = "200 OK"
    resp._headers = {}

    middleware = TraceMiddleware()
    with (
        mock.patch("ddtrace.contrib.internal.falcon.middleware.tracer.current_span", return_value=span),
        mock.patch("ddtrace.contrib.internal.falcon.middleware.core.dispatch") as mocked_dispatch,
    ):
        middleware.process_response(req, resp, mock.MagicMock(), req_succeeded=True)

    web_finish_calls = [c for c in mocked_dispatch.call_args_list if c.args and c.args[0] == "web.request.finish"]
    assert web_finish_calls, "expected a web.request.finish dispatch"
    # ``route`` is the second-to-last positional in the args tuple.
    args_tuple = web_finish_calls[-1].args[1]
    route_arg = args_tuple[-2]
    assert route_arg == want_route
