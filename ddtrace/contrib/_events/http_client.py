from dataclasses import dataclass
from enum import Enum
from typing import Callable
from typing import Mapping
from typing import MutableMapping
from typing import Optional
from typing import Sequence
from typing import Union

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection
from ddtrace.internal.settings.integration import IntegrationConfig


log = get_logger(__name__)


JsonType = Union[None, bool, int, float, str, Sequence["JsonType"], Mapping[str, "JsonType"]]


class HttpClientEvents(Enum):
    HTTP_REQUEST = "http.client.request"
    HTTPX_REQUEST = "httpx.request"
    HTTPX_SINGLE_REQUEST = "httpx.single.request"


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
    response_body_json: Optional[Callable[[], JsonType]] = event_field(default=None)
    config: "IntegrationConfig" = event_field()

    def __post_init__(self):
        self.span_name = schematize_url_operation(
            self.http_operation, protocol="http", direction=SpanDirection.OUTBOUND
        )


@dataclass
class HttpClientSingleRequestEvent(Event):
    """HTTP client request event

    This represents individual requests in a single http client call.
    Examples are managed auth flows and redirect requests.
    """

    event_name = HttpClientEvents.HTTPX_SINGLE_REQUEST.value

    url: str = event_field()
    request_method: str = event_field()
    request_headers: MutableMapping[str, str] = event_field()
    request_body: Callable[[], Union[str, bytes]] = event_field()
    response_headers: MutableMapping[str, str] = event_field(default_factory=dict)
    response_status_code: Optional[int] = event_field(default=None)
