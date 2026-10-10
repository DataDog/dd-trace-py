from unittest import mock

import pytest

from ddtrace import config
from ddtrace.appsec._contrib.httpx import subscribers
from ddtrace.contrib._events.http_client import HttpClientEvents
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.internal import core


def _request_context(response, analyze_body):
    event = HttpClientRequestEvent(
        http_operation="http.request",
        service=None,
        component="httpx",
        integration_config=config.httpx,
        request_method="GET",
        request_headers={},
        request_url="https://example.test/",
        query="",
    )
    event.set_response(response)
    ctx = core.ExecutionContext(event.event_name, event=event)
    ctx.set_item(subscribers.APPSEC_SSRF_ANALYZE_BODY_KEY, analyze_body)
    return ctx


@pytest.mark.parametrize("analyze_body", [False, True])
def test_response_body_is_parsed_only_when_analysis_is_selected(analyze_body):
    response = mock.Mock(status_code=200, reason="OK", headers={})
    response.json.return_value = {"response": "body"}
    ctx = _request_context(response, analyze_body)

    with (
        mock.patch.object(subscribers, "get_rasp_capability", return_value=True),
        mock.patch.object(subscribers, "call_waf_callback") as call_waf_callback,
    ):
        subscribers.AppSecHttpxRequestContextSubscriber.on_ended(ctx, (None, None, None))

    assert response.json.call_count == int(analyze_body)
    addresses = call_waf_callback.call_args.args[0]
    if analyze_body:
        assert addresses["DOWN_RES_BODY"] == {"response": "body"}
    else:
        assert "DOWN_RES_BODY" not in addresses


def test_disabled_httpx_request_completes_without_further_analysis():
    response = mock.Mock(status_code=200, reason="OK", headers={})
    event = _request_context(response, analyze_body=True).event
    subscriber = subscribers.AppSecHttpxRequestContextSubscriber
    with (
        mock.patch.object(subscribers, "get_rasp_capability", return_value=True) as capability,
        mock.patch.object(subscribers, "_get_asm_context", return_value=mock.Mock()),
        mock.patch.object(subscribers, "should_analyze_body_response", return_value=True),
        mock.patch.object(subscribers, "call_waf_callback") as call_waf_callback,
        mock.patch.object(subscriber, "_on_context_ended", wraps=subscriber._on_context_ended) as complete,
    ):
        subscriber.register()
        try:
            with core.context_with_event(event, context_name_override=HttpClientEvents.HTTPX_REQUEST.value) as ctx:
                capability.return_value = False
                subscriber.unregister()
            ctx.dispatch_ended_event()
        finally:
            subscriber.unregister()

    complete.assert_called_once_with(ctx, (None, None, None))
    response.json.assert_not_called()
    call_waf_callback.assert_not_called()
    assert ctx._event is None
