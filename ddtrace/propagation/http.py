import logging

from ..context import Context

log = logging.getLogger(__name__)

# HTTP headers one should set for distributed tracing.
# These are cross-language (eg: Python, Go and other implementations should honor these)
HTTP_HEADER_TRACE_ID = 'x-datadog-trace-id'
HTTP_HEADER_PARENT_ID = 'x-datadog-parent-id'
HTTP_HEADER_SAMPLING_PRIORITY = 'x-datadog-sampling-priority'


class HTTPPropagator(object):
    """A HTTP Propagator using HTTP headers as carrier."""

    def inject(self, span_context, headers):
        """Inject Context attributes that have to be propagated as HTTP headers.

        Here is an example using `requests`::

            import requests
            from ddtrace.propagation.http import HTTPPropagator

            def parent_call():
                with tracer.trace("parent_span") as span:
                    headers = {}
                    HTTPPropagator.inject(span.context, headers)
                    url = "<some RPC endpoint>"
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

    def extract(self, headers):
        """Extract a Context from HTTP headers into a new Context.

        Here is an example from a web endpoint::

            from ddtrace.propagation.http import HTTPPropagator

            def my_controller(url, headers):
                context = HTTPPropagator.extract(headers)
                tracer.context_provider.activate(context)

                with tracer.trace("my_controller") as span:
                    span.set_meta('http.url', url)

        :param dict headers: HTTP headers to extract tracing attributes.
        :return: New `Context` with propagated attributes.
        """
        if not headers:
            return Context()

        try:
            trace_id = int(headers.get(HTTP_HEADER_TRACE_ID, 0))
            parent_span_id = int(headers.get(HTTP_HEADER_PARENT_ID, 0))
            sampling_priority = headers.get(HTTP_HEADER_SAMPLING_PRIORITY)
            if sampling_priority is not None:
                sampling_priority = int(sampling_priority)

            return Context(
                trace_id=trace_id,
                span_id=parent_span_id,
                sampling_priority=sampling_priority,
            )
        # If headers are invalid and cannot be parsed, return a new context and log the issue.
        except Exception as error:
            try:
                log.debug(
                    "invalid x-datadog-* headers, trace-id: %s, parent-id: %s, priority: %s, error: %s",
                    headers.get(HTTP_HEADER_TRACE_ID, 0),
                    headers.get(HTTP_HEADER_PARENT_ID, 0),
                    headers.get(HTTP_HEADER_SAMPLING_PRIORITY),
                    error,
                )
            # We might fail on string formatting errors ; in that case only format the first error
            except Exception:
                log.debug(error)
            return Context()
