import mock

from ddtrace.contrib._events.web_framework import WebFrameworkEvents
from ddtrace.internal import core


REQUEST_STARTING_PATH = "/web-request-starting"


def test_microvm_run_hook_request(client):
    """traced_get_response() must dispatch method/path before request tracing starts.

    get_response() runs before URL resolution, so this covers unmatched routes too (see
    test_django_request_not_found).
    """
    with mock.patch("ddtrace.contrib.internal.django.response.core.dispatch", wraps=core.dispatch) as m:
        resp = client.post(REQUEST_STARTING_PATH)

    assert resp.status_code == 404
    m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("POST", REQUEST_STARTING_PATH))


def test_other_request(client):
    with mock.patch("ddtrace.contrib.internal.django.response.core.dispatch", wraps=core.dispatch) as m:
        resp = client.get("/")

    assert resp.status_code == 200
    m.assert_any_call(WebFrameworkEvents.WEB_REQUEST_STARTING.value, ("GET", "/"))
