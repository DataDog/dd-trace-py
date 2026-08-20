from falcon import testing
import mock

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.internal import core

from .app import get_app


REQUEST_STARTING_PATH = "/web-request-starting"


def _client():
    return testing.TestClient(get_app())


def test_microvm_run_hook_request():
    """process_request() must dispatch method/path before request tracing starts.

    No route is registered here: process_request() runs before resource routing, so it still
    fires on the 404 (see test_404 in test_suite.py).
    """
    with mock.patch("ddtrace.contrib.internal.falcon.middleware.core.dispatch", wraps=core.dispatch) as m:
        response = _client().simulate_post(REQUEST_STARTING_PATH)

    assert response.status[:3] == "404"
    m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("POST", REQUEST_STARTING_PATH))


def test_other_request():
    with mock.patch("ddtrace.contrib.internal.falcon.middleware.core.dispatch", wraps=core.dispatch) as m:
        response = _client().simulate_get("/200")

    assert response.status[:3] == "200"
    m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("GET", "/200"))
