import time

import pytest

from ddtrace._trace._request_queuing import SPAN_HTTP_PROXY_QUEUE
from ddtrace._trace._request_queuing import SPAN_HTTP_PROXY_REQUEST
from ddtrace._trace._request_queuing import create_request_queuing_spans_if_headers_exist
from ddtrace._trace._request_queuing import get_request_queue_start_time
from ddtrace._trace.span import Span
from ddtrace.internal.core import ExecutionContext


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
    result = get_request_queue_start_time(headers, now=time.time())
    if expected is None:
        assert result is None
    else:
        assert result == pytest.approx(expected)


def test_get_request_queue_start_time_prefers_x_request_start():
    headers = {"x-request-start": "t=1512379167.574", "x-queue-start": "t=1512379100.000"}
    assert get_request_queue_start_time(headers, now=time.time()) == pytest.approx(1512379167.574)


def test_get_request_queue_start_time_rejects_future_timestamp():
    now = time.time()
    future = now + 3600
    headers = {"x-request-start": str(int(future * 1000))}
    assert get_request_queue_start_time(headers, now=now) is None


def test_get_request_queue_start_time_no_headers():
    assert get_request_queue_start_time({}, now=time.time()) is None


def test_create_request_queuing_spans_if_headers_exist(tracer) -> None:
    ctx = ExecutionContext("test")
    start_time = time.time() - 5
    headers = {"x-request-start": str(int(start_time * 1000))}

    create_request_queuing_spans_if_headers_exist(ctx, headers)

    request_span: Span = ctx.get_item("inferred_proxy_span")
    assert request_span is not None
    assert request_span.name == SPAN_HTTP_PROXY_REQUEST
    assert request_span.span_type == "proxy"
    assert request_span.get_tag("span.kind") == "proxy"
    assert request_span.get_tag("component") == "http_proxy"
    assert request_span.get_tag("operation") == "request"
    assert request_span.start_ns == pytest.approx(int(start_time * 1000) / 1000 * 1e9, abs=1e7)
    assert request_span.duration_ns is None  # not finished yet; caller finishes it via the callback

    finish_callback = ctx.get_item("inferred_proxy_finish_callback")
    assert finish_callback is not None
    finished_span = tracer.start_span("wsgi.request", child_of=request_span)
    finished_span.resource = "GET 200"
    finished_span.finish()
    finish_callback(finished_span)
    assert request_span.duration_ns is not None
    assert request_span.resource == "GET 200"


def test_create_request_queuing_spans_creates_finished_queue_span(tracer) -> None:
    ctx = ExecutionContext("test")
    start_time = time.time() - 2
    headers = {"x-queue-start": str(int(start_time * 1000))}

    create_request_queuing_spans_if_headers_exist(ctx, headers)

    request_span: Span = ctx.get_item("inferred_proxy_span")
    queue_span: Span = ctx.get_item("request_queuing_queue_span")

    assert queue_span.name == SPAN_HTTP_PROXY_QUEUE
    assert queue_span.parent_id == request_span.span_id
    assert queue_span.get_tag("operation") == "queue"
    # The queue span should be already finished, with a duration approximating
    # "now minus the proxy's timestamp" -- i.e. roughly 2 seconds, not zero.
    assert queue_span.duration_ns is not None
    assert queue_span.duration_ns >= 1e9  # at least ~1s
    assert queue_span.get_metric("_dd.measured") == 1


def test_create_request_queuing_spans_no_headers_noop(tracer) -> None:
    ctx = ExecutionContext("test")
    create_request_queuing_spans_if_headers_exist(ctx, {})
    assert ctx.get_item("inferred_proxy_span") is None


def test_create_request_queuing_spans_missing_header_noop(tracer) -> None:
    ctx = ExecutionContext("test")
    create_request_queuing_spans_if_headers_exist(ctx, {"some-other-header": "value"})
    assert ctx.get_item("inferred_proxy_span") is None
