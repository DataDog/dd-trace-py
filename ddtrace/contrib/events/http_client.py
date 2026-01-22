from dataclasses import dataclass
from enum import Enum

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import COMPONENT
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


@dataclass
class HttpClientRequestEvent:
    def __init__(self, req):
        self.event_name = "http.request"
        self.span_type = SpanTypes.HTTP
        self.span_name = schematize_url_operation("http.request", protocol="http", direction=SpanDirection.OUTBOUND)
        self.tags = {COMPONENT: config.httpx.integration_name, SPAN_KIND: SpanKind.CLIENT}
        self.call_trace = True
        self.request = req


class HttpClientEvents(Enum):
    HTTP_REQUEST = "http.request"
