import mock

from ddtrace.internal.runtime import MICROVM_RUN_HOOK_PATH


def test_microvm_run_hook_request(client):
    """traced_get_response() must pass method/path to maybe_refresh_identity(), so it
    detects the MicroVM /run hook without app changes. get_response() runs before URL
    resolution, so this covers unmatched routes too (see test_django_request_not_found).
    """
    with mock.patch("ddtrace.contrib.internal.django.response.maybe_refresh_identity") as m:
        resp = client.post(MICROVM_RUN_HOOK_PATH)

    assert resp.status_code == 404
    m.assert_called_once_with("POST", MICROVM_RUN_HOOK_PATH)


def test_other_request(client):
    with mock.patch("ddtrace.contrib.internal.django.response.maybe_refresh_identity") as m:
        resp = client.get("/")

    assert resp.status_code == 200
    m.assert_called_once_with("GET", "/")
