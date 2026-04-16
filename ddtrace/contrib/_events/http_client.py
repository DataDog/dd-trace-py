from dataclasses import dataclass
from enum import Enum
from typing import Callable
from typing import Optional
from typing import Union

from ddtrace._trace.events import TracingEvent
from ddtrace.contrib._events.http import HttpBaseEvent
from ddtrace.contrib._events.http import HttpRequestBaseEvent
from ddtrace.contrib._events.http import _HttpResponse
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import event_field
from ddtrace.internal.logger import get_logger
from ddtrace.internal.schema import schematize_url_operation
from ddtrace.internal.schema.span_attribute_schema import SpanDirection


log = get_logger(__name__)


class HttpClientEvents(Enum):
    HTTP_REQUEST = "http.client.request"
    HTTPX_REQUEST = "httpx.request"
    HTTP_SEND_REQUEST = "http.client.send_request"
    HTTPX_SEND_REQUEST = "httpx.send_request"


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
    request_body: Callable[[], Union[str, bytes]] = event_field()
