from dataclasses import dataclass
from enum import Enum
from typing import Callable
from typing import Mapping
from typing import MutableMapping
from typing import Optional
from typing import Protocol
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


class _HttpClientResponse(Protocol):
    @property
    def headers(self) -> MutableMapping[str, str]: ...
    @property
    def status_code(self) -> int: ...

    def json(self) -> JsonType: ...


class HttpClientEvents(Enum):
    HTTP_REQUEST = "http.client.request"
    HTTPX_REQUEST = "httpx.request"
    REQUESTS_REQUEST = "requests.request"
    URLLIB3_REQUEST = "urllib3.request"
    HTTP_SEND_REQUEST = "http.client.send_request"
    HTTPX_SEND_REQUEST = "httpx.send_request"
    URLLIB3_SEND_REQUEST = "urllib3.send_request"


@dataclass
class HttpClientBaseEvent(Event):
    url: str = event_field()
    request_method: str = event_field()
    request_headers: MutableMapping[str, str] = event_field()
    response_headers: Mapping[str, str] = event_field(default_factory=dict)
    response_status_code: Optional[int] = event_field(default=None)

    def set_response(self, response: _HttpClientResponse) -> None:
        self.response_status_code = response.status_code
        self.response_headers = response.headers


@dataclass
class HttpClientRequestEvent(HttpClientBaseEvent, TracingEvent):
    """HTTP client request event"""

    event_name = HttpClientEvents.HTTP_REQUEST.value

    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.HTTP

    http_operation: str = event_field()
    query: str = event_field()
    target_host: Optional[str] = event_field()
    config: "IntegrationConfig" = event_field()

    response: Optional[_HttpClientResponse] = event_field(default=None)

    def __post_init__(self):
        self.span_name = schematize_url_operation(
            self.http_operation, protocol="http", direction=SpanDirection.OUTBOUND
        )

    def set_response(self, response: _HttpClientResponse) -> None:
        super().set_response(response)
        self.response = response


@dataclass
class HttpClientSendEvent(HttpClientBaseEvent, Event):
    """HTTP client send event

    This represents individual requests in a single http client call.
    Examples are managed auth flows and redirect requests.
    """

    event_name = HttpClientEvents.HTTP_SEND_REQUEST.value
    request_body: Callable[[], Union[str, bytes]] = event_field()
