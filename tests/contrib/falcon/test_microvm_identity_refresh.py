from falcon import testing
import mock

from ddtrace.internal import core
from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH

from .app import get_app


def _client():
    return testing.TestClient(get_app())


def test_microvm_run_hook_request():
    """process_request() must dispatch method/path before request tracing starts.

    No route is registered here: process_request() runs before resource routing, so it still
    fires on the 404 (see test_404 in test_suite.py).
    """
    with mock.patch("ddtrace.contrib.internal.falcon.middleware.core.dispatch", wraps=core.dispatch) as m:
        response = _client().simulate_post(MICROVM_RUN_HOOK_PATH)

    assert response.status[:3] == "404"
    m.assert_any_call(core.WEB_REQUEST_STARTING, ("POST", MICROVM_RUN_HOOK_PATH))


def test_other_request():
    with mock.patch("ddtrace.contrib.internal.falcon.middleware.core.dispatch", wraps=core.dispatch) as m:
        response = _client().simulate_get("/200")

    assert response.status[:3] == "200"
    m.assert_any_call(core.WEB_REQUEST_STARTING, ("GET", "/200"))
