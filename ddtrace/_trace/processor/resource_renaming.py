from typing import Union

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings._config import config
from ddtrace.internal.utils.endpoint import SimplifiedEndpointComputer
from ddtrace.internal.utils.endpoint import path_source_tag_value


class ResourceRenamingProcessor(SpanProcessor):
    def __init__(self):
        self.simplified_endpoint_computer = SimplifiedEndpointComputer()

    def on_span_start(self, span: Span):
        pass

    def on_span_finish(self, span: Span):
        if not span._is_top_level or span.span_type not in (SpanTypes.WEB, SpanTypes.HTTP, SpanTypes.SERVERLESS):
            return

        status: Union[str, int, float, None] = None
        if config._otel_trace_semantics_enabled:
            status = span._get_attribute(otel_http.RESPONSE_STATUS_CODE)
        if status is None:
            status = span.get_tag(http.STATUS_CODE)
        is_404 = status == "404" or status == 404

        route = span.get_tag(http.ROUTE)

        if not is_404 and (not route or config._trace_resource_renaming_always_simplified_endpoint):
            url = path_source_tag_value(span)
            if not url:
                return
            endpoint = self.simplified_endpoint_computer.from_url(url)
            span._set_attribute(http.ENDPOINT, endpoint)
