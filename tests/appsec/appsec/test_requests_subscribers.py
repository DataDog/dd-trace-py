from unittest import mock

import pytest

import ddtrace.appsec._common_module_patches as cmp
from ddtrace.appsec._common_module_patches import patch_common_modules
from ddtrace.appsec._common_module_patches import unpatch_common_modules
from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.appsec._contrib.httplib import listen as httplib_listen
from ddtrace.appsec._contrib.httplib import subscribers as httplib_appsec
from ddtrace.appsec._contrib.httplib import unlisten as httplib_unlisten
from ddtrace.appsec._contrib.requests import listen as requests_listen
from ddtrace.appsec._contrib.requests import subscribers as requests_appsec
from ddtrace.appsec._contrib.requests import unlisten as requests_unlisten
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib.internal.httplib.events import unpatch_http_client_events
from ddtrace.internal import core
from ddtrace.internal._exceptions import BlockingException
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.settings.standalone import standalone_config
from tests.appsec.utils import blocking_waf_result


@pytest.fixture
def requests_subscriber():
    activation = requests_appsec.AppSecRequestsSubscriber.activation
    httplib_was_active = core.has_listeners("context.started." + HttpClientEvents.HTTPLIB_REQUEST.value)
    httplib_listen()
    requests_listen()
    try:
        yield
    finally:
        requests_unlisten()
        httplib_unlisten()
        if httplib_was_active:
            httplib_listen()
        else:
            unpatch_http_client_events()
        if activation is not None:
            requests_appsec.AppSecRequestsSubscriber.activation = activation
            requests_appsec.AppSecRequestsSubscriber.register()
            requests_appsec.AppSecRequestsSendSubscriber.register()


@pytest.mark.parametrize("reactivate", [False, True])
def test_requests_operation_preserves_original_url_result_and_lazy_body(requests_subscriber, reactivate):
    import requests

    from ddtrace.contrib.internal.requests.patch import patch
    from ddtrace.contrib.internal.requests.patch import unpatch

    class OriginalUrl(str):
        pass

    url = OriginalUrl("http://example.test/path")
    response = requests.Response()
    response.status_code = 200
    response.json = mock.Mock()
    original_context = core.current

    def send(prepared, **kwargs):
        assert core.find_item(requests_appsec.APPSEC_REQUESTS_CONTEXT_KEY).pending_url is url
        assert kwargs["stream"] is True
        if reactivate:
            requests_unlisten()
            requests_listen()
        return response

    patch()
    try:
        with (
            mock.patch.object(requests_appsec, "get_rasp_capability", return_value=True),
            mock.patch.object(requests_appsec, "_get_asm_context", return_value=mock.Mock()),
            mock.patch.object(requests_appsec, "should_analyze_body_response", return_value=reactivate),
            mock.patch.object(requests_appsec, "call_waf_callback") as waf,
        ):
            session = requests.Session()
            session.send = send
            assert session.request("GET", url, stream=True) is response
            response.json.assert_not_called()
            assert waf.call_count == int(not reactivate)

            error = requests.exceptions.ConnectionError("transport failed")
            session.send = mock.Mock(side_effect=error)
            with pytest.raises(requests.exceptions.ConnectionError) as raised:
                session.request("GET", url)
            assert raised.value is error
            assert core.current is original_context
    finally:
        unpatch()


@pytest.mark.parametrize("block", [False, True])
def test_requests_analyzes_once_before_transport(requests_subscriber, block):
    import http.client
    import io

    import requests
    import urllib3.connection

    from ddtrace import tracer
    from ddtrace.appsec._asm_request_context import _RASP_SUBCONTEXT
    from ddtrace.contrib.internal.httplib.patch import patch as httplib_patch
    from ddtrace.contrib.internal.httplib.patch import unpatch as httplib_unpatch
    from ddtrace.contrib.internal.requests.patch import patch as requests_patch
    from ddtrace.contrib.internal.requests.patch import unpatch as requests_unpatch

    sock = mock.Mock()
    sock.makefile.return_value = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
    inspected = []

    def waf(addresses=None, **kwargs):
        inspected.append((kwargs["rule_type"], addresses, core.find_item(_RASP_SUBCONTEXT)))
        return (
            blocking_waf_result()
            if block and (addresses or {}).get(EXPLOIT_PREVENTION.ADDRESS.SSRF) == "http://example.test/path"
            else None
        )

    original_context = core.current
    apm_enabled = standalone_config.apm_tracing_enabled
    unpatch_common_modules()
    requests_patch()
    httplib_patch()
    patch_common_modules()
    try:
        with (
            mock.patch.object(asm_config, "_asm_enabled", True),
            mock.patch.object(asm_config, "_ep_enabled", True),
            mock.patch.object(cmp, "get_rasp_capability", return_value=True),
            mock.patch.object(requests_appsec, "get_rasp_capability", return_value=True),
            mock.patch.object(httplib_appsec, "get_rasp_capability", return_value=True),
            mock.patch.object(httplib_appsec, "get_active_asm_context", return_value=mock.Mock(downstream_requests=0)),
            mock.patch.object(requests_appsec, "_get_asm_context", return_value=mock.Mock()),
            mock.patch.object(cmp, "get_active_asm_context", return_value=mock.Mock(downstream_requests=0)),
            mock.patch.object(requests_appsec, "should_analyze_body_response", return_value=False),
            mock.patch.object(cmp, "get_blocked", return_value={"status_code": 403}),
            mock.patch.object(cmp, "call_waf_callback", side_effect=waf),
            mock.patch.object(httplib_appsec, "should_analyze_body_response", return_value=False) as body_sampling,
            mock.patch.object(httplib_appsec, "call_waf_callback", side_effect=waf),
            mock.patch.object(requests_appsec, "call_waf_callback", side_effect=waf),
            mock.patch.object(urllib3.connection.HTTPConnection, "_new_conn", return_value=sock) as transport,
        ):
            tracer.configure(apm_tracing_disabled=True)
            try:
                assert standalone_config.apm_opt_out
                with tracer.trace("incoming") as span:
                    span.context.sampling_priority = -1

                    def independent(host, path):
                        connection = http.client.HTTPConnection(host)
                        connection._send_request = mock.Mock(return_value=None)
                        connection.request("GET", path)

                    def auth(prepared):
                        independent("auth.test", "/auth")
                        return prepared

                    def hook(response, **kwargs):
                        independent("hook.test", "/hook")

                    def operation():
                        return requests.get(
                            "http://example.test/path",
                            proxies={"http": None, "https": None},
                            auth=auth,
                            hooks={"response": hook},
                        )

                    if block:
                        with pytest.raises(BlockingException):
                            operation()
                        transport.assert_not_called()
                    else:
                        response = operation()
                        assert response.status_code == 200
                        assert response.content == b"{}"
                        transport.assert_called_once()
                    assert [rule for rule, _, _ in inspected] == (
                        [EXPLOIT_PREVENTION.TYPE.SSRF_REQ, EXPLOIT_PREVENTION.TYPE.SSRF_REQ]
                        if block
                        else [
                            EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
                            EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
                            EXPLOIT_PREVENTION.TYPE.SSRF_REQ,
                            EXPLOIT_PREVENTION.TYPE.SSRF_RES,
                        ]
                    )
                    assert inspected[0][1][EXPLOIT_PREVENTION.ADDRESS.SSRF] == "http://auth.test/auth"
                    assert inspected[1][1][EXPLOIT_PREVENTION.ADDRESS.SSRF] == "http://example.test/path"
                    assert inspected[0][2] is not inspected[1][2]
                    assert inspected[-1][2] is inspected[1][2]
                    if not block:
                        assert inspected[2][1][EXPLOIT_PREVENTION.ADDRESS.SSRF] == "http://hook.test/hook"
                        assert len({id(holder) for _, _, holder in inspected}) == 3
                    assert body_sampling.call_count == (1 if block else 2)
                    assert core.current is original_context
                    assert span.context.sampling_priority == -1
            finally:
                tracer.configure(apm_tracing_disabled=not apm_enabled)
    finally:
        requests_unpatch()
        httplib_unpatch()
        unpatch_common_modules()


def test_requests_publishes_the_original_url_and_body_with_tracing_disabled(requests_subscriber):
    """Session.send skips its traced context when tracing is off; the transport hop must not depend on it."""
    import io

    import requests
    import urllib3.connection

    from ddtrace import tracer
    from ddtrace.contrib.internal.requests.patch import patch as requests_patch
    from ddtrace.contrib.internal.requests.patch import unpatch as requests_unpatch

    sock = mock.Mock()
    sock.makefile.return_value = io.BytesIO(
        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
    )
    inspected = []

    def waf(addresses=None, **kwargs):
        inspected.append((kwargs["rule_type"], addresses))

    env = mock.Mock(downstream_requests=0)
    unpatch_common_modules()
    requests_patch()
    patch_common_modules()
    try:
        with (
            mock.patch.object(tracer, "enabled", False),
            mock.patch.object(standalone_config, "apm_tracing_enabled", True),
            mock.patch.object(cmp, "get_rasp_capability", return_value=True),
            mock.patch.object(cmp, "get_active_asm_context", return_value=env),
            mock.patch.object(cmp, "call_waf_callback", side_effect=waf),
            mock.patch.object(requests_appsec, "get_rasp_capability", return_value=True),
            mock.patch.object(requests_appsec, "_get_asm_context", return_value=env),
            mock.patch.object(requests_appsec, "should_analyze_body_response", return_value=True),
            mock.patch.object(requests_appsec, "call_waf_callback", side_effect=waf),
            mock.patch.object(httplib_appsec, "get_rasp_capability", return_value=True),
            mock.patch.object(httplib_appsec, "get_active_asm_context", return_value=env),
            mock.patch.object(httplib_appsec, "call_waf_callback", side_effect=waf),
            mock.patch.object(urllib3.connection.HTTPConnection, "_new_conn", return_value=sock),
        ):
            assert not standalone_config.apm_opt_out
            # The host's case is normalized during preparation, so only the original input keeps it.
            response = requests.post("http://Example.test/path", json={"q": 1}, proxies={"http": None, "https": None})
            assert response.status_code == 200
    finally:
        requests_unpatch()
        unpatch_common_modules()

    assert [rule for rule, _ in inspected] == [EXPLOIT_PREVENTION.TYPE.SSRF_REQ, EXPLOIT_PREVENTION.TYPE.SSRF_RES]
    request_addresses = inspected[0][1]
    assert request_addresses[EXPLOIT_PREVENTION.ADDRESS.SSRF] == "http://Example.test/path"
    assert request_addresses["DOWN_REQ_BODY"] == {"q": 1}
    assert inspected[1][1]["DOWN_RES_BODY"] == {}
