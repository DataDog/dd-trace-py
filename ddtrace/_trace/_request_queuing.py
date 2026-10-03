import re
import time
from typing import Mapping
from typing import Optional

from ddtrace._trace._inferred_proxy import INFERRED_SPAN_NAMES
from ddtrace._trace.span import Span
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.propagation.http import _extract_header_value
from ddtrace.propagation.http import _possible_header
from ddtrace.trace import tracer


SPAN_HTTP_SERVER_QUEUE = "http.server.queue"
TAG_COMPONENT_HTTP_PROXY = "http_proxy"

# Headers set by upstream proxies/load balancers (nginx, Heroku's router, Apache, HAProxy, ...)
# denoting when a request was first received, before it reached this process.
POSSIBLE_HEADER_REQUEST_START = _possible_header("x-request-start")
POSSIBLE_HEADER_QUEUE_START = _possible_header("x-queue-start")

# Below this, a parsed timestamp is almost certainly the result of a malformed/unexpected
# header rather than a real point in time (this corresponds to a 2001-09-09 UTC floor).
MINIMUM_ACCEPTABLE_TIME_VALUE = 1_000_000_000

# Upper bound on the queue time we are willing to report. Anything larger is far more likely
# to be a stale, replayed, or mis-scaled header than a real queuing delay.
MAXIMUM_QUEUE_TIME_S = 60 * 60

_NON_DIGIT_RE = re.compile(r"[^0-9]")


def _parse_queue_start_time(header_value: str, now: float) -> Optional[float]:
    """Parse a proxy queue-start header value into a Unix timestamp (seconds).

    Upstream proxies disagree on the unit used for this header:
      - nginx sends seconds with a millisecond fraction, e.g. t=1512379167.574
      - Apache sends whole microseconds since the epoch, e.g. t=1570633834463123
      - Heroku's router sends whole milliseconds since the epoch, e.g. 1570634024294

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

    if time_value < MINIMUM_ACCEPTABLE_TIME_VALUE:
        return None

    # Reject timestamps in the future, which would indicate significant clock skew
    # between the upstream proxy and this host rather than a real queuing delay.
    if time_value > now:
        return None

    if now - time_value > MAXIMUM_QUEUE_TIME_S:
        return None

    return time_value


def get_request_queue_start_time(headers: dict[str, str], now: Optional[float] = None) -> Optional[float]:
    """Return the Unix timestamp (seconds) at which an upstream proxy received this request.

    headers must already be normalized to lowercase keys.
    """
    header_value = _extract_header_value(POSSIBLE_HEADER_REQUEST_START, headers) or _extract_header_value(
        POSSIBLE_HEADER_QUEUE_START, headers
    )
    if header_value is None:
        return None

    return _parse_queue_start_time(header_value, now if now is not None else time.time())


def _is_service_entry_web_span(span: Span) -> bool:
    if span.span_type != SpanTypes.WEB:
        return False
    # Only the first web span of the request in this process: either the local root, or a direct
    # child of an inferred proxy span (AWS API Gateway, Azure APIM, ...).
    parent = span._parent
    return parent is None or parent.name in INFERRED_SPAN_NAMES


def create_request_queue_span_if_headers_exist(
    request_span: Span, headers: Optional[Mapping[str, str]]
) -> Optional[Span]:
    """Create an http.server.queue span if a queue-start header is present.

    The queue span starts at the upstream proxy's timestamp and finishes when the
    application's request span started, so its duration is the time spent queued before the
    request reached application code.

    It is created as a sibling of the request span rather than a parent, the same way the
    serverless cold start span sits next to the aws.lambda span: the request span keeps its
    existing parent, so it remains the local root that drives sampling and APM trace metrics, and
    keeps its resource, status and error. The queue span is created as a local child of the
    request span so that it shares its trace and sampling decision, and then re-parented on the
    wire onto the request span's parent. It uses its own name as its resource, since the
    request's resource is usually only final once the request completes.
    """
    if not headers or not _is_service_entry_web_span(request_span):
        return None

    normalized_headers = {key.lower(): value for key, value in headers.items()}
    start_time = get_request_queue_start_time(normalized_headers, now=request_span.start)
    if start_time is None:
        return None

    queue_span = tracer.start_span(
        SPAN_HTTP_SERVER_QUEUE,
        service=request_span.service,
        span_type=SpanTypes.PROXY,
        child_of=request_span,
    )
    queue_span.start_ns = int(start_time * 1e9)
    queue_span.parent_id = request_span.parent_id
    queue_span._set_attribute(COMPONENT, TAG_COMPONENT_HTTP_PROXY)
    queue_span._set_attribute(SPAN_KIND, SpanKind.SERVER)
    queue_span._finish_ns(request_span.start_ns)
    return queue_span
