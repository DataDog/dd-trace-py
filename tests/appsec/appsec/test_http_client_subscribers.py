from unittest.mock import MagicMock
from unittest.mock import call

import pytest

from ddtrace.appsec._constants import EXPLOIT_PREVENTION
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.contrib._events.http_client import HttpClientSendEvent
from ddtrace.internal import core
from tests.appsec.utils import asm_context


class _RequestsResponse(object):
    def __init__(self, status_code, headers, payload=None):
        self.status_code = status_code
        self.headers = headers
        self._payload = payload

    def json(self):
        return self._payload


class _Urllib3Response(object):
    def __init__(self, status, headers):
        self.status = status
        self.headers = headers


@pytest.fixture
def http_client_subscribers():
    import ddtrace.appsec._contrib.http_client.subscribers as subscribers

    return subscribers


def test_requests_and_urllib3_contexts_trigger_shared_ssrf_callbacks(monkeypatch, http_client_subscribers):
    waf_callback = MagicMock()

    monkeypatch.setattr(http_client_subscribers, "_get_rasp_capability", lambda capability: capability == "ssrf")
    monkeypatch.setattr(http_client_subscribers, "should_analyze_body_response", lambda asm_context: True)
    monkeypatch.setattr(http_client_subscribers, "call_waf_callback", waf_callback)
    monkeypatch.setattr(http_client_subscribers, "get_blocked", lambda: None)

    with asm_context(config={"_asm_enabled": True}):
        with core.context_with_event(
            HttpClientRequestEvent(
                http_operation="requests.request",
                service="requests",
                component="requests",
                config={},
                request_method="POST",
                request_headers={"content-type": "application/json"},
                url="https://example.com/path?q=1",
                query="q=1",
                target_host="example.com",
            ),
            context_name_override=HttpClientEvents.REQUESTS_REQUEST.value,
        ) as request_ctx:
            with core.context_with_event(
                HttpClientSendEvent(
                    url="https://example.com/path?q=1",
                    request_method="POST",
                    request_headers={"content-type": "application/json"},
                    request_body=lambda: '{"key":"value"}',
                ),
                context_name_override=HttpClientEvents.URLLIB3_SEND_REQUEST.value,
            ) as send_ctx:
                send_ctx.event.response_status_code = 302
                send_ctx.event.response_headers = {"location": "/redirect"}

            request_ctx.event.set_response(_RequestsResponse(200, {"content-type": "application/json"}, {"ok": True}))

    assert waf_callback.call_args_list[0].kwargs["rule_type"] == EXPLOIT_PREVENTION.TYPE.SSRF_REQ
    assert waf_callback.call_args_list[0].args[0] == {
        EXPLOIT_PREVENTION.ADDRESS.SSRF: "https://example.com/path?q=1",
        "DOWN_REQ_METHOD": "POST",
        "DOWN_REQ_HEADERS": {"content-type": "application/json"},
        "DOWN_REQ_BODY": {"key": "value"},
    }
    assert waf_callback.call_args_list[1].kwargs["rule_type"] == EXPLOIT_PREVENTION.TYPE.SSRF_RES
    assert waf_callback.call_args_list[1].args[0] == {
        "DOWN_RES_STATUS": "302",
        "DOWN_RES_HEADERS": {"location": "/redirect"},
    }
    assert waf_callback.call_args_list[2].kwargs["rule_type"] == EXPLOIT_PREVENTION.TYPE.SSRF_RES
    assert waf_callback.call_args_list[2].args[0] == {
        "DOWN_RES_STATUS": "200",
        "DOWN_RES_HEADERS": {"content-type": "application/json"},
        "DOWN_RES_BODY": {"ok": True},
    }


def test_urllib3_send_context_skips_without_outer_request_context(monkeypatch, http_client_subscribers):
    waf_callback = MagicMock()

    monkeypatch.setattr(http_client_subscribers, "_get_rasp_capability", lambda capability: capability == "ssrf")
    monkeypatch.setattr(http_client_subscribers, "call_waf_callback", waf_callback)
    monkeypatch.setattr(http_client_subscribers, "get_blocked", lambda: None)

    with asm_context(config={"_asm_enabled": True}):
        with core.context_with_event(
            HttpClientSendEvent(
                url="https://example.com/path?q=1",
                request_method="GET",
                request_headers={},
                request_body=lambda: None,
            ),
            context_name_override=HttpClientEvents.URLLIB3_SEND_REQUEST.value,
        ) as send_ctx:
            send_ctx.event.response_status_code = 302
            send_ctx.event.response_headers = {"location": "/redirect"}

    waf_callback.assert_not_called()


def test_nested_requests_contexts_only_report_final_response_once(monkeypatch, http_client_subscribers):
    waf_callback = MagicMock()

    monkeypatch.setattr(http_client_subscribers, "_get_rasp_capability", lambda capability: capability == "ssrf")
    monkeypatch.setattr(http_client_subscribers, "should_analyze_body_response", lambda asm_context: True)
    monkeypatch.setattr(http_client_subscribers, "call_waf_callback", waf_callback)
    monkeypatch.setattr(http_client_subscribers, "get_blocked", lambda: None)

    with asm_context(config={"_asm_enabled": True}):
        with core.context_with_event(
            HttpClientRequestEvent(
                http_operation="requests.request",
                service="requests",
                component="requests",
                config={},
                request_method="GET",
                request_headers={},
                url="https://example.com/path?q=1",
                query="q=1",
                target_host="example.com",
            ),
            context_name_override=HttpClientEvents.REQUESTS_REQUEST.value,
        ) as outer_ctx:
            with core.context_with_event(
                HttpClientRequestEvent(
                    http_operation="requests.request",
                    service="requests",
                    component="requests",
                    config={},
                    request_method="GET",
                    request_headers={},
                    url="https://example.com/path?q=1",
                    query="q=1",
                    target_host="example.com",
                ),
                context_name_override=HttpClientEvents.REQUESTS_REQUEST.value,
            ) as inner_ctx:
                inner_ctx.event.set_response(
                    _RequestsResponse(200, {"content-type": "application/json"}, {"inner": True})
                )

            outer_ctx.event.set_response(_RequestsResponse(200, {"content-type": "application/json"}, {"ok": True}))

    assert waf_callback.call_args_list == [
        call(
            {
                "DOWN_RES_STATUS": "200",
                "DOWN_RES_HEADERS": {"content-type": "application/json"},
                "DOWN_RES_BODY": {"ok": True},
            },
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
        )
    ]


def test_urllib3_request_context_uses_shared_response_handling(monkeypatch, http_client_subscribers):
    waf_callback = MagicMock()

    monkeypatch.setattr(http_client_subscribers, "_get_rasp_capability", lambda capability: capability == "ssrf")
    monkeypatch.setattr(http_client_subscribers, "should_analyze_body_response", lambda asm_context: False)
    monkeypatch.setattr(http_client_subscribers, "call_waf_callback", waf_callback)
    monkeypatch.setattr(http_client_subscribers, "get_blocked", lambda: None)

    with asm_context(config={"_asm_enabled": True}):
        with core.context_with_event(
            HttpClientRequestEvent(
                http_operation="urllib3.request",
                service="urllib3",
                component="urllib3",
                config={},
                request_method="GET",
                request_headers={},
                url="https://example.com/path?q=1",
                query="q=1",
                target_host="example.com",
            ),
            context_name_override=HttpClientEvents.URLLIB3_REQUEST.value,
        ) as request_ctx:
            request_ctx.event.response_status_code = 204
            request_ctx.event.response_headers = {"content-type": "application/json"}
            request_ctx.event.response = _Urllib3Response(204, {"content-type": "application/json"})

    assert waf_callback.call_args_list == [
        call(
            {
                "DOWN_RES_STATUS": "204",
                "DOWN_RES_HEADERS": {"content-type": "application/json"},
            },
            rule_type=EXPLOIT_PREVENTION.TYPE.SSRF_RES,
        )
    ]
