from typing import Dict

from ddtrace import config as global_config

from ..context import Context
from .b3 import B3HTTPPropagator
from .base_http_propagator import BaseHTTPPropagator
from .datadog import DatadogHTTPPropagator


class HTTPPropagator(BaseHTTPPropagator):
    """
    A Propagator that wraps zero or more or more HTTP propagators.

    Currently supported HTTP propagators are:

        - b3
        - datadog
    """

    @staticmethod
    def inject(span_context, headers):
        # type: (Context, Dict[str, str]) -> None
        """
        Inject Context attributes that have to be propagated as HTTP headers.

        Where multiple HTTP propagators are configured, each will be called in turn

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
        if "datadog" in global_config.propagation_style_inject:
            DatadogHTTPPropagator.inject(span_context, headers)

        if "b3" in global_config.propagation_style_inject:
            B3HTTPPropagator.inject(span_context, headers)

    @staticmethod
    def extract(headers):
        # type: (Dict[str,str]) -> Context
        """
        Extract a Context from HTTP headers into a new Context.

        Where multiple HTTP propagators are configured, the first match found will be used

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
        empty_ctx = Context()
        if not headers:
            return empty_ctx

        if "datadog" in global_config.propagation_style_extract:
            ctx = DatadogHTTPPropagator.extract(headers)
            if ctx != empty_ctx:
                return ctx

        if "b3" in global_config.propagation_style_extract:
            ctx = B3HTTPPropagator.extract(headers)
            if ctx != empty_ctx:
                return ctx

        return empty_ctx
