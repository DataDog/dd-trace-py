import mock

from ddtrace.internal import core
from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH


def test_microvm_run_hook_request(client):
    """traced_get_response() must dispatch method/path before request tracing starts.

    get_response() runs before URL resolution, so this covers unmatched routes too (see
    test_django_request_not_found).
    """
    with mock.patch("ddtrace.contrib.internal.django.response.core.dispatch", wraps=core.dispatch) as m:
        resp = client.post(MICROVM_RUN_HOOK_PATH)

    assert resp.status_code == 404
    m.assert_any_call(core.WEB_REQUEST_STARTING, ("POST", MICROVM_RUN_HOOK_PATH))


def test_other_request(client):
    with mock.patch("ddtrace.contrib.internal.django.response.core.dispatch", wraps=core.dispatch) as m:
        resp = client.get("/")

    assert resp.status_code == 200
    m.assert_any_call(core.WEB_REQUEST_STARTING, ("GET", "/"))
