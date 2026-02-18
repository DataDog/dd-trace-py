from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from typing import MutableMapping
from typing import Optional

from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import TracingEvent
from ddtrace.internal.core.events import event_field
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


if TYPE_CHECKING:
    from ddtrace.internal.settings.integration import IntegrationConfig

log = get_logger(__name__)


class HttpClientEvents(Enum):
    HTTP_REQUEST = "http.client.request"


@dataclass
class HttpClientRequestEvent(TracingEvent):
    """HTTP client request event"""

    event_name = HttpClientEvents.HTTP_REQUEST.value

    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.HTTP

    http_operation: str = event_field()
    url: str = event_field()
    query: str = event_field()
    target_host: Optional[str] = event_field()
    request_method: str = event_field()
    request_headers: MutableMapping[str, str] = event_field()
    response_headers: MutableMapping[str, str] = event_field(default_factory=dict)
    response_status_code: Optional[int] = event_field(default=None)
    config: "IntegrationConfig" = event_field()

    measured: bool = True

    def __post_init__(self):
        self.span_name = schematize_url_operation(
            self.http_operation, protocol="http", direction=SpanDirection.OUTBOUND
        )
