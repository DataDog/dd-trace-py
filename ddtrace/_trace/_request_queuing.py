import re
import time
from typing import Mapping
from typing import Optional

from ddtrace import config
from ddtrace._trace.span import Span
from ddtrace.constants import _SPAN_MEASURED_KEY
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.propagation.http import _extract_header_value
from ddtrace.propagation.http import _possible_header
from ddtrace.trace import tracer


# Span names, mirrored from the Ruby tracer's Rack `request_queuing` feature so that
# queue-time traces look identical across languages.
# https://github.com/DataDog/dd-trace-rb/blob/master/lib/datadog/tracing/contrib/rack/ext.rb
SPAN_HTTP_PROXY_REQUEST = "http.proxy.request"
SPAN_HTTP_PROXY_QUEUE = "http.proxy.queue"

TAG_COMPONENT_HTTP_PROXY = "http_proxy"
TAG_OPERATION_HTTP_PROXY_REQUEST = "request"
TAG_OPERATION_HTTP_PROXY_QUEUE = "queue"
TAG_OPERATION = "operation"

# Headers set by upstream proxies/load balancers (nginx, Heroku's router, Apache, HAProxy, ...)
# denoting when a request was first received, before it reached this process.
POSSIBLE_HEADER_REQUEST_START = _possible_header("x-request-start")
POSSIBLE_HEADER_QUEUE_START = _possible_header("x-queue-start")

# Below this, a parsed timestamp is almost certainly the result of a malformed/unexpected
# header rather than a real point in time (this corresponds to a 2001-09-09 UTC floor).
MINIMUM_ACCEPTABLE_TIME_VALUE = 1_000_000_000

_NON_DIGIT_RE = re.compile(r"[^0-9]")


def _parse_queue_start_time(header_value: str, now: float) -> Optional[float]:
    """Parse a proxy queue-start header value into a Unix timestamp (seconds).

    Upstream proxies disagree on the unit used for this header:
      - nginx sends seconds with a millisecond fraction, e.g. ``t=1512379167.574``
      - Apache sends whole microseconds since the epoch, e.g. ``t=1570633834463123``
      - Heroku's router sends whole milliseconds since the epoch, e.g. ``1570634024294``

    Rather than special-case each format, take the first 10 digits as whole seconds and
    up to the next 6 digits as the fractional part. This happens to line up correctly for
    all three formats above, since 10 digits covers seconds-since-epoch until the year 2286,
    and the remaining digits estimated as sub-second precision naturally fall out of place
    for milliseconds/microseconds inputs. Ported from the equivalent Ruby implementation:
    https://github.com/DataDog/dd-trace-rb/blob/master/lib/datadog/tracing/contrib/rack/request_queue.rb
    """
    digits = _NON_DIGIT_RE.sub("", header_value or "")
    if not digits:
        return None

    try:
        time_value = float(f"{digits[:10]}.{digits[10:16]}")
    except ValueError:
        return None

    if time_value == 0 or time_value < MINIMUM_ACCEPTABLE_TIME_VALUE:
        return None

    # Reject timestamps in the future, which would indicate significant clock skew
    # between the upstream proxy and this host rather than a real queuing delay.
    if time_value > now:
        return None

    return time_value


def get_request_queue_start_time(headers: Mapping[str, str], now: Optional[float] = None) -> Optional[float]:
    """Return the Unix timestamp (seconds) at which an upstream proxy received this request.

    ``headers`` must already be normalized to lowercase keys.
    """
    header_value = _extract_header_value(POSSIBLE_HEADER_REQUEST_START, headers) or _extract_header_value(
        POSSIBLE_HEADER_QUEUE_START, headers
    )
    if header_value is None:
        return None

    return _parse_queue_start_time(header_value, now if now is not None else time.time())


def _tag_proxy_span(span: Span, operation: str) -> None:
    span._set_attribute(COMPONENT, TAG_COMPONENT_HTTP_PROXY)
    span._set_attribute("span.kind", SpanKind.PROXY)
    span._set_attribute(TAG_OPERATION, operation)


def create_request_queuing_spans_if_headers_exist(ctx, headers: Mapping[str, str]) -> None:
    """Create the `http.proxy.request` / `http.proxy.queue` span pair if a queue-start header is present.

    This mirrors the Ruby tracer's Rack `request_queuing` feature: a virtual parent span
    (``http.proxy.request``) represents the request from the upstream proxy's point of view,
    starting at the proxy's timestamp; a child span (``http.proxy.queue``) is started at that
    same timestamp and immediately finished, so its duration is exactly the time spent queued
    upstream of this process, before the request reached application code.
    """
    if not headers:
        return

    normalized_headers = {key.lower(): value for key, value in headers.items()}
    start_time = get_request_queue_start_time(normalized_headers)
    if start_time is None:
        return

    start_ns = int(start_time * 1e9)

    request_span = tracer.start_span(
        SPAN_HTTP_PROXY_REQUEST,
        service=config._get_service(),
        span_type=SpanTypes.PROXY,
        activate=True,
        child_of=tracer.current_trace_context(),
    )
    request_span.start_ns = start_ns
    _tag_proxy_span(request_span, TAG_OPERATION_HTTP_PROXY_REQUEST)

    queue_span = tracer.start_span(
        SPAN_HTTP_PROXY_QUEUE,
        service=config._get_service(),
        span_type=SpanTypes.PROXY,
        child_of=request_span,
    )
    queue_span.start_ns = start_ns
    _tag_proxy_span(queue_span, TAG_OPERATION_HTTP_PROXY_QUEUE)
    queue_span._set_attribute(_SPAN_MEASURED_KEY, 1)
    # Finish immediately: this span should only measure time spent in queue,
    # not the time spent processing the request itself.
    queue_span.finish()

    def finish_callback(_: object) -> None:
        request_span.finish()

    ctx.set_item("inferred_proxy_span", request_span)
    ctx.set_item("inferred_proxy_finish_callback", finish_callback)
    ctx.set_item("request_queuing_queue_span", queue_span)
