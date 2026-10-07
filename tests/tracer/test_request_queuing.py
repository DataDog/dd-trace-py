import time

import pytest

from ddtrace._trace._request_queuing import MAXIMUM_QUEUE_TIME_S
from ddtrace._trace._request_queuing import SPAN_HTTP_SERVER_QUEUE
from ddtrace._trace._request_queuing import create_request_queue_span_if_headers_exist
from ddtrace._trace._request_queuing import get_request_queue_start_time
from ddtrace.ext import SpanTypes
from ddtrace.trace import Context


@pytest.mark.parametrize(
    "header_name,header_value,expected",
    [
        # nginx: seconds, with a millisecond fraction
        ("x-request-start", "t=1512379167.574", 1512379167.574),
        # Apache: whole microseconds since the epoch
        ("x-request-start", "t=1570633834463123", 1570633834.463123),
        # Heroku's router: whole milliseconds since the epoch
        ("x-queue-start", "1570634024294", 1570634024.294),
        # unparseable/garbage header
        ("x-request-start", "garbage", None),
        # empty header
        ("x-request-start", "", None),
        # timestamp far too small to be a real epoch time
        ("x-request-start", "t=123.456", None),
    ],
)
def test_get_request_queue_start_time(header_name, header_value, expected):
    headers = {header_name: header_value}
    # Pin "now" just after the fixture timestamps so the max queue time bound doesn't reject them.
    now = (expected or 1570634024.294) + 1
    result = get_request_queue_start_time(headers, now=now)
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_get_request_queue_start_time_prefers_x_request_start():
    headers = {"x-request-start": "t=1512379167.574", "x-queue-start": "t=1512379100.000"}
    assert get_request_queue_start_time(headers, now=1512379168) == pytest.approx(1512379167.574)


def test_get_request_queue_start_time_rejects_future_timestamp():
    now = time.time()
    headers = {"x-request-start": str(int((now + 3600) * 1000))}
    assert get_request_queue_start_time(headers, now=now) is None


def test_get_request_queue_start_time_rejects_queue_time_over_maximum():
    now = time.time()
    headers = {"x-request-start": str(int((now - MAXIMUM_QUEUE_TIME_S - 1) * 1000))}
    assert get_request_queue_start_time(headers, now=now) is None


def test_get_request_queue_start_time_no_headers():
    assert get_request_queue_start_time({}, now=time.time()) is None


def test_create_request_queue_span(tracer, test_spans):
    start_time = time.time() - 2
    headers = {"X-Request-Start": str(int(start_time * 1000))}

    with tracer.trace("web.request", service="my-web", resource="GET /users", span_type=SpanTypes.WEB) as request_span:
        queue_span = create_request_queue_span_if_headers_exist(request_span, headers)

    assert queue_span is not None
    assert queue_span.name == SPAN_HTTP_SERVER_QUEUE
    assert queue_span.span_type == "proxy"
    assert queue_span.get_tag("span.kind") == "server"
    assert queue_span.get_tag("component") == "http_proxy"
    assert queue_span.service == "my-web"
    assert queue_span.resource == SPAN_HTTP_SERVER_QUEUE
    assert queue_span.get_metric("_dd.measured") is None
    assert queue_span.get_tag("http.method") is None

    # Same trace, but a sibling of the request span rather than its child or parent.
    assert queue_span.trace_id == request_span.trace_id
    assert queue_span.parent_id is None
    assert request_span.parent_id is None

    # Starts at the proxy timestamp and ends exactly when the application started handling the request.
    assert queue_span.start_ns == pytest.approx(int(start_time * 1000) * 1e6, abs=1e6)
    assert queue_span.start_ns + queue_span.duration_ns == request_span.start_ns
    assert queue_span.duration_ns >= 1e9

    # The request span is untouched: still the local root and top-level, so it drives sampling and stats.
    assert request_span._local_root is request_span
    assert request_span._is_top_level
    assert queue_span._local_root is request_span

    spans = test_spans.pop()
    assert {s.name for s in spans} == {"web.request", SPAN_HTTP_SERVER_QUEUE}


def test_create_request_queue_span_shares_sampling_decision(tracer, test_spans):
    headers = {"x-request-start": str(int((time.time() - 1) * 1000))}

    with tracer.trace("web.request", span_type=SpanTypes.WEB) as request_span:
        queue_span = create_request_queue_span_if_headers_exist(request_span, headers)

    assert queue_span is not None
    assert queue_span.context.sampling_priority == request_span.context.sampling_priority
    assert queue_span.context.sampling_priority is not None


def test_create_request_queue_span_keeps_distributed_parent(tracer, test_spans):
    headers = {"x-request-start": str(int((time.time() - 1) * 1000))}
    remote = Context(trace_id=1234, span_id=5678, sampling_priority=1, is_remote=True)

    request_span = tracer.start_span("web.request", child_of=remote, span_type=SpanTypes.WEB, activate=True)
    queue_span = create_request_queue_span_if_headers_exist(request_span, headers)
    request_span.finish()

    assert queue_span is not None
    assert request_span.trace_id == queue_span.trace_id == 1234
    assert request_span.parent_id == queue_span.parent_id == 5678


@pytest.mark.parametrize("parent_name,expect_span", [("aws.apigateway", True), ("some.other.span", False)])
def test_create_request_queue_span_only_on_service_entry_span(tracer, test_spans, parent_name, expect_span):
    headers = {"x-request-start": str(int((time.time() - 1) * 1000))}

    with tracer.trace(parent_name) as parent:
        with tracer.trace("web.request", span_type=SpanTypes.WEB) as request_span:
            queue_span = create_request_queue_span_if_headers_exist(request_span, headers)

    if not expect_span:
        assert queue_span is None
        return
    assert queue_span is not None
    assert queue_span.parent_id == parent.span_id


def test_create_request_queue_span_ignores_non_web_spans(tracer, test_spans):
    headers = {"x-request-start": str(int((time.time() - 1) * 1000))}
    with tracer.trace("worker.job") as span:
        assert create_request_queue_span_if_headers_exist(span, headers) is None


@pytest.mark.parametrize("headers", [None, {}, {"some-other-header": "value"}, {"x-request-start": "garbage"}])
def test_create_request_queue_span_noop_without_valid_header(tracer, test_spans, headers):
    with tracer.trace("web.request", span_type=SpanTypes.WEB) as span:
        assert create_request_queue_span_if_headers_exist(span, headers) is None
    assert [s.name for s in test_spans.pop()] == ["web.request"]
