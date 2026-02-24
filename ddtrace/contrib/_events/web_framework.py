from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from typing import MutableMapping
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import event_field
from ddtrace.internal.schema import SpanDirection
from ddtrace.internal.schema import schematize_url_operation


if TYPE_CHECKING:
    from ddtrace.internal.settings.integration import IntegrationConfig


class WebFrameworkEvents(Enum):
    WEB_REQUEST = "web.request"
    WEB_REQUEST_FINAL_TAGS = "web.request.final_tags"


@dataclass
class WebRequestEvent(TracingEvent):
    """HTTP client request event"""

    event_name = WebFrameworkEvents.WEB_REQUEST.value

    span_type = SpanTypes.WEB
    span_kind = SpanKind.SERVER

    url_operation: str = event_field()
    allow_default_resource: bool = event_field(default=False)
    set_resource: bool = event_field(default=False)

    request_method: str = event_field()
    request_url: str = event_field()
    request_headers: MutableMapping[str, str] = event_field()
    request_query: str = event_field()
    request_route: Optional[str] = event_field(default=None)

    response_status_code: Optional[int] = event_field(default=None)
    response_headers: MutableMapping[str, str] = event_field(default_factory=dict)

    integration_config: "IntegrationConfig" = event_field()

    def __post_init__(self):
        self.span_name = schematize_url_operation(self.url_operation, protocol="http", direction=SpanDirection.INBOUND)
