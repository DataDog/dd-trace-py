from typing import Dict
from typing import FrozenSet
from typing import Optional
from typing import Text
from typing import cast

from ddtrace import config

from ..constants import AUTO_KEEP
from ..constants import AUTO_REJECT
from ..constants import USER_KEEP
from ..context import Context
from ..internal._tagset import TagsetDecodeError
from ..internal._tagset import TagsetEncodeError
from ..internal._tagset import TagsetMaxSizeDecodeError
from ..internal._tagset import TagsetMaxSizeEncodeError
from ..internal._tagset import decode_tagset_string
from ..internal._tagset import encode_tagset_values
from ..internal.compat import ensure_str
from ..internal.compat import ensure_text
from ..internal.constants import PROPAGATION_STYLE_B3
from ..internal.constants import PROPAGATION_STYLE_B3_SINGLE_HEADER
from ..internal.constants import PROPAGATION_STYLE_DATADOG
from ..internal.logger import get_logger
from ..internal.sampling import validate_sampling_decision
from ..span import _MetaDictType
from ._utils import get_wsgi_header


log = get_logger(__name__)

# HTTP headers one should set for distributed tracing.
# These are cross-language (eg: Python, Go and other implementations should honor these)
HTTP_HEADER_TRACE_ID = "x-datadog-trace-id"
HTTP_HEADER_PARENT_ID = "x-datadog-parent-id"
HTTP_HEADER_SAMPLING_PRIORITY = "x-datadog-sampling-priority"
HTTP_HEADER_ORIGIN = "x-datadog-origin"
_HTTP_HEADER_B3_SINGLE = "b3"
_HTTP_HEADER_B3_TRACE_ID = "x-b3-traceid"
_HTTP_HEADER_B3_SPAN_ID = "x-b3-spanid"
_HTTP_HEADER_B3_SAMPLED = "x-b3-sampled"
_HTTP_HEADER_B3_FLAGS = "x-b3-flags"
_HTTP_HEADER_TAGS = "x-datadog-tags"


def _possible_header(header):
    # type: (str) -> FrozenSet[str]
    return frozenset([header, get_wsgi_header(header).lower()])


# Note that due to WSGI spec we have to also check for uppercased and prefixed
# versions of these headers
POSSIBLE_HTTP_HEADER_TRACE_IDS = _possible_header(HTTP_HEADER_TRACE_ID)
POSSIBLE_HTTP_HEADER_PARENT_IDS = _possible_header(HTTP_HEADER_PARENT_ID)
POSSIBLE_HTTP_HEADER_SAMPLING_PRIORITIES = _possible_header(HTTP_HEADER_SAMPLING_PRIORITY)
POSSIBLE_HTTP_HEADER_ORIGIN = _possible_header(HTTP_HEADER_ORIGIN)
_POSSIBLE_HTTP_HEADER_TAGS = frozenset([_HTTP_HEADER_TAGS, get_wsgi_header(_HTTP_HEADER_TAGS).lower()])
_POSSIBLE_HTTP_HEADER_B3_SINGLE_HEADER = _possible_header(_HTTP_HEADER_B3_SINGLE)
_POSSIBLE_HTTP_HEADER_B3_TRACE_IDS = _possible_header(_HTTP_HEADER_B3_TRACE_ID)
_POSSIBLE_HTTP_HEADER_B3_SPAN_IDS = _possible_header(_HTTP_HEADER_B3_SPAN_ID)
_POSSIBLE_HTTP_HEADER_B3_SAMPLEDS = _possible_header(_HTTP_HEADER_B3_SAMPLED)
_POSSIBLE_HTTP_HEADER_B3_FLAGS = _possible_header(_HTTP_HEADER_B3_FLAGS)


def _extract_header_value(possible_header_names, headers, default=None):
    # type: (FrozenSet[str], Dict[str, str], Optional[str]) -> Optional[str]
    for header in possible_header_names:
        if header in headers:
            return ensure_str(headers[header], errors="backslashreplace")

    return default


def _b3_id_to_dd_id(b3_id):
    # type: (str) -> int
    """Helper to convert B3 trace/span hex ids into Datadog compatible ints

    If the id is > 64 bit then truncate the trailing 64 bit.

    "463ac35c9f6413ad48485a3953bb6124" -> "48485a3953bb6124" -> 5208512171318403364
    """
    return int(b3_id[-16:], 16)


def _dd_id_to_b3_id(dd_id):
    # type: (int) -> str
    """Helper to convert Datadog trace/span int ids into B3 compatible hex ids"""
    # DEV: `hex(dd_id)` will give us `0xDEADBEEF`
    # DEV: this gives us lowercase hex, which is what we want
    return "{:x}".format(dd_id)


class _DatadogMultiHeader:
    """Helper class for injecting/extract Datadog multi header format

    Headers:

      - ``x-datadog-trace-id`` the context trace id as a uint64 integer
      - ``x-datadog-parent-id`` the context current span id as a uint64 integer
      - ``x-datadog-sampling-priority`` integer representing the sampling decision.
        ``<= 0`` (Reject) or ``> 1`` (Keep)
      - ``x-datadog-origin`` optional name of origin Datadog product which initiated the request
      - ``x-datadog-tags`` optional tracer tags

    Restrictions:

      - Trace tag key-value pairs in ``x-datadog-tags`` are extracted from incoming requests.
      - Only trace tags with keys prefixed with ``_dd.p.`` are propagated.
      - The trace tag keys must be printable ASCII characters excluding space, comma, and equals.
      - The trace tag values must be printable ASCII characters excluding comma. Leading and
        trailing spaces are trimmed.
    """

    _X_DATADOG_TAGS_EXTRACT_REJECT = frozenset(["_dd.p.upstream_services"])

    @staticmethod
    def _is_valid_datadog_trace_tag_key(key):
        return key.startswith("_dd.p.")

    @staticmethod
    def _inject(span_context, headers):
        # type: (Context, Dict[str, str]) -> None
        if span_context.trace_id is None or span_context.span_id is None:
            log.debug("tried to inject invalid context %r", span_context)
            return

        headers[HTTP_HEADER_TRACE_ID] = str(span_context.trace_id)
        headers[HTTP_HEADER_PARENT_ID] = str(span_context.span_id)
        sampling_priority = span_context.sampling_priority
        # Propagate priority only if defined
        if sampling_priority is not None:
            headers[HTTP_HEADER_SAMPLING_PRIORITY] = str(span_context.sampling_priority)
        # Propagate origin only if defined
        if span_context.dd_origin is not None:
            headers[HTTP_HEADER_ORIGIN] = ensure_text(span_context.dd_origin)

        if not config._x_datadog_tags_enabled:
            span_context._meta["_dd.propagation_error"] = "disabled"
            return

        # Do not try to encode tags if we have already tried and received an error
        if "_dd.propagation_error" in span_context._meta:
            return

        # Only propagate trace tags which means ignoring the _dd.origin
        tags_to_encode = {
            # DEV: Context._meta is a _MetaDictType but we need Dict[str, str]
            ensure_str(k): ensure_str(v)
            for k, v in span_context._meta.items()
            if _DatadogMultiHeader._is_valid_datadog_trace_tag_key(k)
        }  # type: Dict[Text, Text]

        if tags_to_encode:
            try:
                headers[_HTTP_HEADER_TAGS] = encode_tagset_values(
                    tags_to_encode, max_size=config._x_datadog_tags_max_length
                )
            except TagsetMaxSizeEncodeError:
                # We hit the max size allowed, add a tag to the context to indicate this happened
                span_context._meta["_dd.propagation_error"] = "inject_max_size"
                log.warning("failed to encode x-datadog-tags", exc_info=True)
            except TagsetEncodeError:
                # We hit an encoding error, add a tag to the context to indicate this happened
                span_context._meta["_dd.propagation_error"] = "encoding_error"
                log.warning("failed to encode x-datadog-tags", exc_info=True)

    @staticmethod
    def _extract(headers):
        # type: (Dict[str, str]) -> Optional[Context]
        trace_id = _extract_header_value(
            POSSIBLE_HTTP_HEADER_TRACE_IDS,
            headers,
        )
        if trace_id is None:
            return None

        parent_span_id = _extract_header_value(
            POSSIBLE_HTTP_HEADER_PARENT_IDS,
            headers,
            default="0",
        )
        sampling_priority = _extract_header_value(
            POSSIBLE_HTTP_HEADER_SAMPLING_PRIORITIES,
            headers,
        )
        origin = _extract_header_value(
            POSSIBLE_HTTP_HEADER_ORIGIN,
            headers,
        )

        meta = None
        tags_value = _extract_header_value(
            _POSSIBLE_HTTP_HEADER_TAGS,
            headers,
            default="",
        )
        if tags_value:
            # Do not fail if the tags are malformed
            try:
                meta = {
                    k: v
                    for (k, v) in decode_tagset_string(tags_value).items()
                    if (
                        k not in _DatadogMultiHeader._X_DATADOG_TAGS_EXTRACT_REJECT
                        and _DatadogMultiHeader._is_valid_datadog_trace_tag_key(k)
                    )
                }
            except TagsetMaxSizeDecodeError:
                meta = {
                    "_dd.propagation_error": "extract_max_size",
                }
                log.warning("failed to decode x-datadog-tags", exc_info=True)
            except TagsetDecodeError:
                meta = {
                    "_dd.propagation_error": "decoding_error",
                }
                log.debug("failed to decode x-datadog-tags: %r", tags_value, exc_info=True)

        # Try to parse values into their expected types
        try:
            if sampling_priority is not None:
                sampling_priority = int(sampling_priority)  # type: ignore[assignment]
            else:
                sampling_priority = sampling_priority

            if meta:
                meta = validate_sampling_decision(meta)

            return Context(
                # DEV: Do not allow `0` for trace id or span id, use None instead
                trace_id=int(trace_id) or None,
                span_id=int(parent_span_id) or None,  # type: ignore[arg-type]
                sampling_priority=sampling_priority,  # type: ignore[arg-type]
                dd_origin=origin,
                # DEV: This cast is needed because of the type requirements of
                # span tags and trace tags which are currently implemented using
                # the same type internally (_MetaDictType).
                meta=cast(_MetaDictType, meta),
            )
        except (TypeError, ValueError):
            log.debug(
                (
                    "received invalid x-datadog-* headers, "
                    "trace-id: %r, parent-id: %r, priority: %r, origin: %r, tags:%r"
                ),
                trace_id,
                parent_span_id,
                sampling_priority,
                origin,
                tags_value,
            )
        return None


class _B3MultiHeader:
    """Helper class to inject/extract B3 Multi-Headers

    https://github.com/openzipkin/b3-propagation/blob/3e54cda11620a773d53c7f64d2ebb10d3a01794c/README.md#multiple-headers

    Example::

        X-B3-TraceId: 80f198ee56343ba864fe8b2a57d3eff7
        X-B3-ParentSpanId: 05e3ac9a4f6e3b90
        X-B3-SpanId: e457b5a2e4d86bd1
        X-B3-Sampled: 1


    Headers:

      - ``X-B3-TraceId`` header is encoded as 32 or 16 lower-hex characters.
      - ``X-B3-SpanId`` header is encoded as 16 lower-hex characters.
      - ``X-B3-Sampled`` header value of ``0`` means Deny, ``1`` means Accept, and absent means to defer.
      - ``X-B3-Flags`` header is used to set ``1`` meaning Debug or an Accept.

    Restrictions:

      - ``X-B3-Sampled`` and ``X-B3-Flags`` should never both be set

    Implementation details:

      - Sampling priority gets encoded as:
        - ``sampling_priority <= 0`` -> ``X-B3-Sampled: 0``
        - ``sampling_priority == 1`` -> ``X-B3-Sampled: 1``
        - ``sampling_priority > 1`` -> ``X-B3-Flags: 1``
      - Sampling priority gets decoded as:
        - ``X-B3-Sampled: 0`` -> ``sampling_priority = 0``
        - ``X-B3-Sampled: 1`` -> ``sampling_priority = 1``
        - ``X-B3-Flags: 1`` -> ``sampling_priority = 2``
      - ``X-B3-TraceId`` is not required, will use ``None`` when not present
      - ``X-B3-SpanId`` is not required, will use ``None`` when not present
    """

    @staticmethod
    def _inject(span_context, headers):
        # type: (Context, Dict[str, str]) -> None
        if span_context.trace_id is None or span_context.span_id is None:
            log.debug("tried to inject invalid context %r", span_context)
            return

        headers[_HTTP_HEADER_B3_TRACE_ID] = _dd_id_to_b3_id(span_context.trace_id)
        headers[_HTTP_HEADER_B3_SPAN_ID] = _dd_id_to_b3_id(span_context.span_id)
        sampling_priority = span_context.sampling_priority
        # Propagate priority only if defined
        if sampling_priority is not None:
            if sampling_priority <= 0:
                headers[_HTTP_HEADER_B3_SAMPLED] = "0"
            elif sampling_priority == 1:
                headers[_HTTP_HEADER_B3_SAMPLED] = "1"
            elif sampling_priority > 1:
                headers[_HTTP_HEADER_B3_FLAGS] = "1"

    @staticmethod
    def _extract(headers):
        # type: (Dict[str, str]) -> Optional[Context]
        trace_id_val = _extract_header_value(
            _POSSIBLE_HTTP_HEADER_B3_TRACE_IDS,
            headers,
        )
        if trace_id_val is None:
            return None

        span_id_val = _extract_header_value(
            _POSSIBLE_HTTP_HEADER_B3_SPAN_IDS,
            headers,
        )
        sampled = _extract_header_value(
            _POSSIBLE_HTTP_HEADER_B3_SAMPLEDS,
            headers,
        )
        flags = _extract_header_value(
            _POSSIBLE_HTTP_HEADER_B3_FLAGS,
            headers,
        )

        # Try to parse values into their expected types
        try:
            # DEV: We are allowed to have only x-b3-sampled/flags
            # DEV: Do not allow `0` for trace id or span id, use None instead
            trace_id = None
            span_id = None
            if trace_id_val is not None:
                trace_id = _b3_id_to_dd_id(trace_id_val) or None
            if span_id_val is not None:
                span_id = _b3_id_to_dd_id(span_id_val) or None

            sampling_priority = None
            if sampled is not None:
                if sampled == "0":
                    sampling_priority = AUTO_REJECT
                elif sampled == "1":
                    sampling_priority = AUTO_KEEP
            if flags == "1":
                sampling_priority = USER_KEEP

            return Context(
                trace_id=trace_id,
                span_id=span_id,
                sampling_priority=sampling_priority,
            )
        except (TypeError, ValueError):
            log.debug(
                "received invalid x-b3-* headers, " "trace-id: %r, span-id: %r, sampled: %r, flags: %r",
                trace_id_val,
                span_id_val,
                sampled,
                flags,
            )
        return None


class _B3SingleHeader:
    """Helper class to inject/extract B3 Single Header

    https://github.com/openzipkin/b3-propagation/blob/3e54cda11620a773d53c7f64d2ebb10d3a01794c/README.md#single-header

    Format::

        b3={TraceId}-{SpanId}-{SamplingState}-{ParentSpanId}

    Example::

        b3: 80f198ee56343ba864fe8b2a57d3eff7-e457b5a2e4d86bd1-1-05e3ac9a4f6e3b90


    Values:

      - ``TraceId`` header is encoded as 32 or 16 lower-hex characters.
      - ``SpanId`` header is encoded as 16 lower-hex characters.
      - ``SamplingState`` header value of ``0`` means Deny, ``1`` means Accept, and ``d`` means Debug
      - ``ParentSpanId`` header is not used/ignored if sent

    Restrictions:

      - ``ParentSpanId`` value is ignored/not used

    Implementation details:

      - Sampling priority gets encoded as:
        - ``sampling_priority <= 0`` -> ``SamplingState: 0``
        - ``sampling_priority == 1`` -> ``SamplingState: 1``
        - ``sampling_priority > 1`` -> ``SamplingState: d``
      - Sampling priority gets decoded as:
        - ``SamplingState: 0`` -> ``sampling_priority = 0``
        - ``SamplingState: 1`` -> ``sampling_priority = 1``
        - ``SamplingState: d`` -> ``sampling_priority = 2``
      - ``TraceId`` is not required, will use ``None`` when not present
      - ``SpanId`` is not required, will use ``None`` when not present
    """

    @staticmethod
    def _inject(span_context, headers):
        # type: (Context, Dict[str, str]) -> None
        if span_context.trace_id is None or span_context.span_id is None:
            log.debug("tried to inject invalid context %r", span_context)
            return

        single_header = "{}-{}".format(_dd_id_to_b3_id(span_context.trace_id), _dd_id_to_b3_id(span_context.span_id))
        sampling_priority = span_context.sampling_priority
        if sampling_priority is not None:
            if sampling_priority <= 0:
                single_header += "-0"
            elif sampling_priority == 1:
                single_header += "-1"
            elif sampling_priority > 1:
                single_header += "-d"
        headers[_HTTP_HEADER_B3_SINGLE] = single_header

    @staticmethod
    def _extract(headers):
        # type: (Dict[str, str]) -> Optional[Context]
        single_header = _extract_header_value(_POSSIBLE_HTTP_HEADER_B3_SINGLE_HEADER, headers)
        if not single_header:
            return None

        trace_id = None
        span_id = None
        sampled = None

        parts = single_header.split("-")
        trace_id_val = None
        span_id_val = None

        # Only SamplingState is provided
        if len(parts) == 1:
            (sampled,) = parts

        # Only TraceId and SpanId are provided
        elif len(parts) == 2:
            trace_id_val, span_id_val = parts

        # Full header, ignore any ParentSpanId present
        elif len(parts) >= 3:
            trace_id_val, span_id_val, sampled = parts[:3]

        # Try to parse values into their expected types
        try:
            # DEV: We are allowed to have only x-b3-sampled/flags
            # DEV: Do not allow `0` for trace id or span id, use None instead
            if trace_id_val is not None:
                trace_id = _b3_id_to_dd_id(trace_id_val) or None
            if span_id_val is not None:
                span_id = _b3_id_to_dd_id(span_id_val) or None

            sampling_priority = None
            if sampled is not None:
                if sampled == "0":
                    sampling_priority = AUTO_REJECT
                elif sampled == "1":
                    sampling_priority = AUTO_KEEP
                elif sampled == "d":
                    sampling_priority = USER_KEEP

            return Context(
                trace_id=trace_id,
                span_id=span_id,
                sampling_priority=sampling_priority,
            )
        except (TypeError, ValueError):
            log.debug(
                "received invalid b3 header, b3: %r",
                single_header,
            )
        return None


class HTTPPropagator(object):
    """A HTTP Propagator using HTTP headers as carrier."""

    @staticmethod
    def inject(span_context, headers):
        # type: (Context, Dict[str, str]) -> None
        """Inject Context attributes that have to be propagated as HTTP headers.

        Here is an example using `requests`::

            import requests
            from ddtrace.propagation.http import HTTPPropagator

            def parent_call():
                with tracer.trace('parent_span') as span:
                    headers = {}
                    HTTPPropagator.inject(span.context, headers)
                    url = '<some RPC endpoint>'
                    r = requests.get(url, headers=headers)

        :param Context span_context: Span context to propagate.
        :param dict headers: HTTP headers to extend with tracing attributes.
        """
        # Not a valid context to propagate
        if span_context.trace_id is None or span_context.span_id is None:
            log.debug("tried to inject invalid context %r", span_context)
            return

        if PROPAGATION_STYLE_DATADOG in config._propagation_style_inject:
            _DatadogMultiHeader._inject(span_context, headers)
        if PROPAGATION_STYLE_B3 in config._propagation_style_inject:
            _B3MultiHeader._inject(span_context, headers)
        if PROPAGATION_STYLE_B3_SINGLE_HEADER in config._propagation_style_inject:
            _B3SingleHeader._inject(span_context, headers)

    @staticmethod
    def extract(headers):
        # type: (Dict[str,str]) -> Context
        """Extract a Context from HTTP headers into a new Context.

        Here is an example from a web endpoint::

            from ddtrace.propagation.http import HTTPPropagator

            def my_controller(url, headers):
                context = HTTPPropagator.extract(headers)
                if context:
                    tracer.context_provider.activate(context)

                with tracer.trace('my_controller') as span:
                    span.set_tag('http.url', url)

        :param dict headers: HTTP headers to extract tracing attributes.
        :return: New `Context` with propagated attributes.
        """
        if not headers:
            return Context()

        try:
            normalized_headers = {name.lower(): v for name, v in headers.items()}
            # Check all styles until we find the first valid match
            # DEV: We want to check them in this specific priority order
            if PROPAGATION_STYLE_DATADOG in config._propagation_style_extract:
                context = _DatadogMultiHeader._extract(normalized_headers)
                if context is not None:
                    return context
            if PROPAGATION_STYLE_B3 in config._propagation_style_extract:
                context = _B3MultiHeader._extract(normalized_headers)
                if context is not None:
                    return context
            if PROPAGATION_STYLE_B3_SINGLE_HEADER in config._propagation_style_extract:
                context = _B3SingleHeader._extract(normalized_headers)
                if context is not None:
                    return context
        except Exception:
            log.debug("error while extracting context propagation headers", exc_info=True)
        return Context()
