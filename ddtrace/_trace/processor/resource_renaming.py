from typing import Optional
from typing import Union

from ddtrace._trace.processor import SpanProcessor
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.otel_semantics import http as otel_http
from ddtrace.internal.settings._config import config
from ddtrace.internal.utils.endpoint import SimplifiedEndpointComputer


# Under OTel semantics, spans not yet converted (for example server spans) still carry http.url.
def path_source_tag_value(span: SpanData) -> Optional[str]:
    path_source_tags = (
        (otel_http.URL_PATH, otel_http.URL_FULL, http.URL) if config._otel_trace_semantics_enabled else (http.URL,)
    )
    return next((value for value in map(span._get_str_attribute, path_source_tags) if value), None)


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
