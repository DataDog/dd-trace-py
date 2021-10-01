from typing import Dict

from ..context import Context
from ..ext import priority
from ..internal.logger import get_logger
from .base_http_propagator import BaseHTTPPropagator
from .utils import extract_header_value
from .utils import get_wsgi_header


log = get_logger(__name__)

HTTP_HEADER_SINGLE = "b3"
HTTP_HEADER_TRACE_ID = "x-b3-traceid"
HTTP_HEADER_SPAN_ID = "x-b3-spanid"
HTTP_HEADER_SAMPLED = "x-b3-sampled"
HTTP_HEADER_DEBUG = "x-b3-flags"

# Note that due to WSGI spec we have to also check for uppercased and prefixed
# versions of these headers
POSSIBLE_HTTP_HEADER_SINGLE = frozenset([HTTP_HEADER_SINGLE, get_wsgi_header(HTTP_HEADER_SINGLE).lower()])
POSSIBLE_HTTP_HEADER_TRACE_IDS = frozenset([HTTP_HEADER_TRACE_ID, get_wsgi_header(HTTP_HEADER_TRACE_ID).lower()])
POSSIBLE_HTTP_HEADER_SPAN_IDS = frozenset([HTTP_HEADER_SPAN_ID, get_wsgi_header(HTTP_HEADER_SPAN_ID).lower()])
POSSIBLE_HTTP_HEADER_SAMPLED = frozenset([HTTP_HEADER_SAMPLED, get_wsgi_header(HTTP_HEADER_SAMPLED).lower()])
POSSIBLE_HTTP_HEADER_DEBUG = frozenset([HTTP_HEADER_DEBUG, get_wsgi_header(HTTP_HEADER_DEBUG).lower()])

# x-b3-sampled accepted truthy values for pre-specification tracers
_SAMPLED_TRUTHY_VALUES = {"1", "True", "true", "d"}

_SAMPLING_PRIORITY_MAP = {
    priority.USER_REJECT: "0",
    priority.AUTO_REJECT: "0",
    priority.AUTO_KEEP: "1",
    priority.USER_KEEP: "1",
}


class B3HTTPPropagator(BaseHTTPPropagator):
    """
    An HTTP Propagator using B3 headers as carrier.

    It is recommended to use HTTPPropagator rather than this class directly
    """

    @staticmethod
    def inject(span_context, headers):
        # type: (Context, Dict[str, str]) -> None
        """
        Inject Context attributes to be propagated as B3 HTTP headers.

        :param Context span_context: Span context to propagate.
        :param dict headers: HTTP headers to extend with tracing attributes.
        """
        sampled = "0"
        if span_context.sampling_priority is not None:
            sampled = _SAMPLING_PRIORITY_MAP[int(span_context.sampling_priority)]

        if span_context.trace_id is not None:
            headers[HTTP_HEADER_TRACE_ID] = format_trace_id(span_context.trace_id)

        if span_context.span_id is not None:
            headers[HTTP_HEADER_SPAN_ID] = format_span_id(span_context.span_id)

        headers[HTTP_HEADER_SAMPLED] = sampled

    @staticmethod
    def extract(headers):
        # type: (Dict[str,str]) -> Context
        """
        Extract a Context from B3 HTTP headers into a new Context.

        :param dict headers: HTTP headers to extract tracing attributes.
        :return: New `Context` with propagated attributes.
        """
        if not headers:
            return Context()

        normalized_headers = {name.lower(): v for name, v in headers.items()}

        single_header = extract_header_value(
            POSSIBLE_HTTP_HEADER_SINGLE,
            normalized_headers,
        )

        if single_header:
            # The b3 spec calls for the sampling state to be
            # "deferred", which is unspecified. This concept does not
            # translate to SpanContext, so we set it as recorded
            sampled = "1"
            fields = single_header.split("-")
            if len(fields) == 1:
                sampled = fields[0]
            elif len(fields) == 2:
                trace_id, span_id = fields
            elif len(fields) == 3:
                trace_id, span_id, sampled = fields
            elif len(fields) == 4:
                trace_id, span_id, sampled, _ = fields
            else:
                return Context()
        else:
            trace_id = extract_header_value(
                POSSIBLE_HTTP_HEADER_TRACE_IDS,
                normalized_headers,
            )  # type: ignore[assignment]
            span_id = extract_header_value(
                POSSIBLE_HTTP_HEADER_SPAN_IDS,
                normalized_headers,
            )  # type: ignore[assignment]
            sampled = extract_header_value(
                POSSIBLE_HTTP_HEADER_SAMPLED,
                normalized_headers,
            )  # type: ignore[assignment]

            if not (trace_id and span_id):
                return Context()

        debug = extract_header_value(
            POSSIBLE_HTTP_HEADER_DEBUG,
            normalized_headers,
        )

        if sampled in _SAMPLED_TRUTHY_VALUES or debug == "1":
            sampling_priority = priority.AUTO_KEEP
        else:
            sampling_priority = priority.AUTO_REJECT

        try:
            return Context(
                trace_id=int(trace_id, 16),
                span_id=int(span_id, 16),
                sampling_priority=sampling_priority,
            )
        except (TypeError, ValueError):
            log.debug(
                "received invalid x-b3-* headers, trace-id: %r, span-id: %r, sampled: %r",
                trace_id,
                span_id,
                sampled,
            )
            return Context()
        except Exception:
            log.debug("error while extracting b3 headers", exc_info=True)
            return Context()


def format_trace_id(trace_id):
    # type: (int) -> str
    """Format the trace id according to b3 specification (128 bit, 32 hex)"""
    return format(trace_id, "032x")


def format_span_id(span_id):
    # type: (int) -> str
    """Format the span id according to b3 specification (64 bit, 16 hex)"""
    return format(span_id, "016x")
