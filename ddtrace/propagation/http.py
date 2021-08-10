from typing import Dict

from ..context import Context
from ..internal.logger import get_logger
from ..utils.http import AnyHeaders
from ..utils.http import BaseHeaders
from ..utils.http import Headers


log = get_logger(__name__)

# HTTP headers one should set for distributed tracing.
# These are cross-language (eg: Python, Go and other implementations should honor these)
HTTP_HEADER_TRACE_ID = "x-datadog-trace-id"
HTTP_HEADER_PARENT_ID = "x-datadog-parent-id"
HTTP_HEADER_SAMPLING_PRIORITY = "x-datadog-sampling-priority"
HTTP_HEADER_ORIGIN = "x-datadog-origin"


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
        # type: (AnyHeaders) -> Context
        """Extract a Context from HTTP headers into a new Context.

        Here is an example from a web endpoint::

            from ddtrace.propagation.http import HTTPPropagator

            def my_controller(url, headers):
                context = HTTPPropagator.extract(headers)
                if context:
                    tracer.context_provider.activate(context)

                with tracer.trace('my_controller') as span:
                    span.set_meta('http.url', url)

        :param dict headers: HTTP headers to extract tracing attributes.
        :return: New `Context` with propagated attributes.
        """
        if len(headers) == 0:
            return Context()

        try:
            if not isinstance(headers, BaseHeaders):
                headers = Headers(headers)

            trace_ids = headers.get(HTTP_HEADER_TRACE_ID)
            if not trace_ids:
                return Context()

            parent_span_ids = headers.get(HTTP_HEADER_PARENT_ID, "0")
            sampling_priorities = headers.get(HTTP_HEADER_SAMPLING_PRIORITY)
            origins = headers.get(HTTP_HEADER_ORIGIN)

            # Try to parse values into their expected types
            try:
                return Context(
                    # DEV: Do not allow `0` for trace id or span id, use None instead
                    trace_id=int(trace_ids[0]) or None,
                    span_id=int(parent_span_ids[0]) or None,
                    sampling_priority=int(sampling_priorities[0]) if sampling_priorities else None,
                    dd_origin=origins[0] if origins else None,
                )
            # If headers are invalid and cannot be parsed, return a new context and log the issue.
            except (TypeError, IndexError, ValueError):
                log.debug(
                    "received invalid x-datadog-* headers, trace-id: %r, parent-id: %r, priority: %r, origin: %r",
                    trace_ids,
                    parent_span_ids,
                    sampling_priorities,
                    origins,
                    exc_info=True,
                )
                return Context()
        except Exception:
            log.debug("error while extracting x-datadog-* headers", exc_info=True)
            return Context()
