from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.settings._config import config
from ddtrace.internal.utils.endpoint import SimplifiedEndpointComputer as SimplifiedEndpointComputer


class ResourceRenamingProcessor(SpanProcessor):
    def __init__(self):
        self.simplified_endpoint_computer = SimplifiedEndpointComputer()

    def on_span_start(self, span: Span):
        pass

    def on_span_finish(self, span: Span):
        if not span._is_top_level or span.span_type not in (SpanTypes.WEB, SpanTypes.HTTP, SpanTypes.SERVERLESS):
            return

        status = span.get_tag(http.STATUS_CODE)
        is_404 = status == "404" or status == 404

        route = span.get_tag(http.ROUTE)

        if not is_404 and (not route or config._trace_resource_renaming_always_simplified_endpoint):
            url = span.get_tag(http.URL)
            endpoint = self.simplified_endpoint_computer.from_url(url)
            span._set_attribute(http.ENDPOINT, endpoint)
