from enum import Enum
from typing import Optional

from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import SpanContextEvent
from ddtrace.internal.core.events import context_event
from ddtrace.internal.core.events import event_field
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


log = get_logger(__name__)


class HttpClientEvents(Enum):
    HTTP_REQUEST = "http.client.request"


@context_event
class HttpClientRequestEvent(SpanContextEvent):
    """HTTP client request event"""

    event_name = HttpClientEvents.HTTP_REQUEST.value
    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.HTTP

    operation_name: str
    url: str = event_field(in_context=True)
    query: object = event_field(in_context=True)
    target_host: Optional[str] = event_field(in_context=True)
    request: object = event_field(in_context=True)
    config: object = event_field(in_context=True)

    def __post_init__(self):
        self.component = self.config.integration_name
        self.span_name = schematize_url_operation(
            self.operation_name, protocol="http", direction=SpanDirection.OUTBOUND
        )
