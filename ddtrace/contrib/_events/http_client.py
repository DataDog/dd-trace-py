from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Generic
from typing import Optional
from typing import TypeVar
from typing import Union

from ddtrace._trace.events import TracingEvent
from ddtrace.contrib._events.http import HttpBaseEvent
from ddtrace.contrib._events.http import HttpRequestBaseEvent
from ddtrace.contrib._events.http import _HttpResponse
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


if TYPE_CHECKING:
    # http.client imports socket, which ddtrace must not load eagerly (serverless and ddtrace.auto).
    from http.client import HTTPConnection


log = get_logger(__name__)


class HttpClientEvents(Enum):
    HTTP_REQUEST = "http.client.request"
    HTTPX_REQUEST = "httpx.request"
    HTTP_SEND_REQUEST = "http.client.send_request"
    HTTPX_SEND_REQUEST = "httpx.send_request"
    REQUESTS_REQUEST = "requests.request"
    REQUESTS_HOP = "requests.hop"
    HTTPLIB_REQUEST = "httplib.request"
    HTTPLIB_RESPONSE = "httplib.response"


@dataclass
class HttpClientRequestEvent(HttpRequestBaseEvent, TracingEvent):
    """HTTP client request event"""

    event_name = HttpClientEvents.HTTP_REQUEST.value

    span_kind = SpanKind.CLIENT
    span_type = SpanTypes.HTTP

    target_host: Optional[str] = event_field(default=None)
    retries_remain: Optional[Union[int, str]] = event_field(default=None)
    server_address: Optional[str] = event_field(default=None)
    response: Optional[_HttpResponse] = event_field(default=None)

    def __post_init__(self):
        self.operation_name = schematize_url_operation(
            self.http_operation, protocol="http", direction=SpanDirection.OUTBOUND
        )

    def set_response(self, response: _HttpResponse) -> None:
        super().set_response(response)
        self.response = response


@dataclass
class HttpClientSendEvent(HttpBaseEvent):
    """HTTP client send event

    This represents individual requests in a single http client call.
    Examples are managed auth flows and redirect requests.
    """

    event_name = HttpClientEvents.HTTP_SEND_REQUEST.value
    # Keep native bodies lazy: clients can provide bytes, text, streams, or no body.
    request_body: Callable[[], Any] = event_field()
    connection: Optional["HTTPConnection"] = event_field(default=None)


@dataclass
class HttpClientOperationEvent(Event):
    """A client API call as the application made it, before the client prepares or sends anything."""

    event_name = HttpClientEvents.REQUESTS_REQUEST.value
    request_url: Any = event_field()
    request_method: Any = event_field()
    response: Optional[Any] = event_field(default=None)


@dataclass
class HttpClientHopEvent(Event):
    """One transport attempt of a client operation: the first send, a redirect, or an auth retry."""

    event_name = HttpClientEvents.REQUESTS_HOP.value
    request_url: Optional[str] = event_field(default=None)


ResponseType = TypeVar("ResponseType")


@dataclass
class HttpClientResponseEvent(Event, Generic[ResponseType]):
    """A native response returned separately from the call that sent the request."""

    event_name = HttpClientEvents.HTTPLIB_RESPONSE.value
    response: ResponseType = event_field()
    # Valid only while the event is dispatched. Returns None for ddtrace's own connections, whose
    # responses are not the application's.
    get_connection: Callable[[], Optional["HTTPConnection"]] = event_field(default=lambda: None)
