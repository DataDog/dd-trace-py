from unittest import mock

import pytest

from ddtrace.appsec._asm_request_context import get_or_create_rasp_subcontext
from ddtrace.appsec._contrib.httpx import subscribers
from ddtrace.contrib._events.http_client import HttpClientRequestEvent
from ddtrace.internal import core


def _request_context(response, analyze_body):
    event = HttpClientRequestEvent(
        http_operation="http.request",
        service=None,
        component="httpx",
        integration_config={},
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


def test_nested_requests_have_distinct_rasp_subcontexts():
    waf = mock.Mock()
    waf.new_subcontext.side_effect = [object(), object()]
    main_context = object()
    with (
        mock.patch.object(subscribers, "get_rasp_capability", return_value=True),
        mock.patch.object(subscribers, "_get_asm_context", return_value=mock.Mock()),
        mock.patch.object(subscribers, "should_analyze_body_response", return_value=False),
    ):
        with core.context_with_data("test.httpx.outer") as outer:
            subscribers.AppSecHttpxRequestContextSubscriber.on_started(outer)
            outer_subcontext = get_or_create_rasp_subcontext(waf, main_context, "ssrf_req")

            # A request made by an event hook is independent of the enclosing request.
            with core.context_with_data("test.httpx.nested") as nested:
                subscribers.AppSecHttpxRequestContextSubscriber.on_started(nested)
                nested_subcontext = get_or_create_rasp_subcontext(waf, main_context, "ssrf_req")
                assert nested_subcontext is not outer_subcontext
                with core.context_with_data("test.httpx.redirect"):
                    assert get_or_create_rasp_subcontext(waf, main_context, "ssrf_req") is nested_subcontext
                    assert get_or_create_rasp_subcontext(waf, main_context, "ssrf_res") is nested_subcontext

            assert get_or_create_rasp_subcontext(waf, main_context, "ssrf_res") is outer_subcontext
    assert waf.new_subcontext.call_count == 2
