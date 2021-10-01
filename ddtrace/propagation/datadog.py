from typing import Dict

from ..context import Context
from ..internal.logger import get_logger
from .base_http_propagator import BaseHTTPPropagator
from .utils import extract_header_value
from .utils import get_wsgi_header


log = get_logger(__name__)

# HTTP headers one should set for distributed tracing.
# These are cross-language (eg: Python, Go and other implementations should honor these)
HTTP_HEADER_TRACE_ID = "x-datadog-trace-id"
HTTP_HEADER_PARENT_ID = "x-datadog-parent-id"
HTTP_HEADER_SAMPLING_PRIORITY = "x-datadog-sampling-priority"
HTTP_HEADER_ORIGIN = "x-datadog-origin"


# Note that due to WSGI spec we have to also check for uppercased and prefixed
# versions of these headers
POSSIBLE_HTTP_HEADER_TRACE_IDS = frozenset([HTTP_HEADER_TRACE_ID, get_wsgi_header(HTTP_HEADER_TRACE_ID).lower()])
POSSIBLE_HTTP_HEADER_PARENT_IDS = frozenset([HTTP_HEADER_PARENT_ID, get_wsgi_header(HTTP_HEADER_PARENT_ID).lower()])
POSSIBLE_HTTP_HEADER_SAMPLING_PRIORITIES = frozenset(
    [HTTP_HEADER_SAMPLING_PRIORITY, get_wsgi_header(HTTP_HEADER_SAMPLING_PRIORITY).lower()]
)
POSSIBLE_HTTP_HEADER_ORIGIN = frozenset([HTTP_HEADER_ORIGIN, get_wsgi_header(HTTP_HEADER_ORIGIN).lower()])


class DatadogHTTPPropagator(BaseHTTPPropagator):
    """
    An HTTP Propagator using Datadog headers as carrier.

    It is recommended to use HTTPPropagator rather than this class directly
    """

    @staticmethod
    def inject(span_context, headers):
        # type: (Context, Dict[str, str]) -> None
        """
        Inject Context attributes to be propagated as Datadog HTTP headers.

        :param Context span_context: Span context to propagate.
        :param dict headers: HTTP headers to extend with tracing attributes.
        """
        headers[HTTP_HEADER_TRACE_ID] = str(span_context.trace_id)
        headers[HTTP_HEADER_PARENT_ID] = str(span_context.span_id)
        sampling_priority = span_context.sampling_priority
        # Propagate priority only if defined
        if sampling_priority is not None:
            headers[HTTP_HEADER_SAMPLING_PRIORITY] = str(span_context.sampling_priority)
        # Propagate origin only if defined
        if span_context.dd_origin is not None:
            headers[HTTP_HEADER_ORIGIN] = str(span_context.dd_origin)

    @staticmethod
    def extract(headers):
        # type: (Dict[str,str]) -> Context
        """
        Extract a Context from Datadog HTTP headers into a new Context.

        :param dict headers: HTTP headers to extract tracing attributes.
        :return: New `Context` with propagated attributes.
        """
        if not headers:
            return Context()

        try:
            normalized_headers = {name.lower(): v for name, v in headers.items()}
            # TODO: Fix variable type changing (mypy)
            trace_id = extract_header_value(
                POSSIBLE_HTTP_HEADER_TRACE_IDS,
                normalized_headers,
            )
            if trace_id is None:
                return Context()

            parent_span_id = extract_header_value(
                POSSIBLE_HTTP_HEADER_PARENT_IDS,
                normalized_headers,
                default="0",
            )
            sampling_priority = extract_header_value(
                POSSIBLE_HTTP_HEADER_SAMPLING_PRIORITIES,
                normalized_headers,
            )
            origin = extract_header_value(
                POSSIBLE_HTTP_HEADER_ORIGIN,
                normalized_headers,
            )

            # Try to parse values into their expected types
            try:
                if sampling_priority is not None:
                    sampling_priority = int(sampling_priority)  # type: ignore[assignment]
                else:
                    sampling_priority = sampling_priority

                return Context(
                    # DEV: Do not allow `0` for trace id or span id, use None instead
                    trace_id=int(trace_id) or None,
                    span_id=int(parent_span_id) or None,  # type: ignore[arg-type]
                    sampling_priority=sampling_priority,  # type: ignore[arg-type]
                    dd_origin=origin,
                )
            # If headers are invalid and cannot be parsed, return a new context and log the issue.
            except (TypeError, ValueError):
                log.debug(
                    "received invalid x-datadog-* headers, trace-id: %r, parent-id: %r, priority: %r, origin: %r",
                    trace_id,
                    parent_span_id,
                    sampling_priority,
                    origin,
                )
                return Context()
        except Exception:
            log.debug("error while extracting x-datadog-* headers", exc_info=True)
            return Context()
