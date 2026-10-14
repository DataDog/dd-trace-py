from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import ClassVar
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


JsonType = Union[None, bool, int, float, str, Sequence["JsonType"], Mapping[str, "JsonType"]]


class _HttpBaseResponse(Protocol):
    @property
    def headers(self) -> MutableMapping[str, str]: ...

    def json(self) -> JsonType: ...


class _HttpResponseWithStatusCode(_HttpBaseResponse, Protocol):
    @property
    def status_code(self) -> int: ...


class _HttpResponseWithStatus(_HttpBaseResponse, Protocol):
    @property
    def status(self) -> int: ...


class _HttpResponseWithReason(_HttpBaseResponse, Protocol):
    @property
    def reason(self) -> str: ...


_HttpResponse = Union[_HttpResponseWithStatus, _HttpResponseWithStatusCode, _HttpResponseWithReason]


@dataclass
class HttpBaseEvent(Event):
    """Common HTTP request/response data.

    This event contains the information needed by both AppSec and APM events when tracing
    http calls (whether client or server).
    """

    request_url: str = event_field()
    request_method: str = event_field()
    request_headers: MutableMapping[str, str] = event_field()

    response_headers: Mapping[str, str] = event_field(default_factory=dict)
    response_status_code: Optional[int] = event_field(default=None)
    response_status_msg: Optional[str] = event_field(default=None)

    def set_response(self, response: _HttpResponse) -> None:
        self.response_status_code = getattr(response, "status_code", None)
        if self.response_status_code is None:
            self.response_status_code = getattr(response, "status", None)
        self.response_status_msg = getattr(response, "reason", None)
        self.response_headers = response.headers


@dataclass
class HttpRequestBaseEvent(HttpBaseEvent):
    """Base event for traced HTTP requests."""

    http_operation: str = event_field()
    query: Optional[str] = event_field(default=None)


class HttpRequestBodyAvailability(Enum):
    NOT_COLLECTED = "not_collected"


@dataclass(frozen=True)
class HttpServerBlockResponse:
    """Consumer-owned response specification; the integration enforces it."""

    status: int = 403
    headers: tuple[tuple[str, str], ...] = (("Content-Type", "text/plain"),)
    body: bytes = b"Blocked"


@dataclass
class HttpServerBeforeHandlerEvent(HttpRequestBaseEvent):
    """Synchronous decision point after routing, before the wrapped handler.

    A single decision consumer may set block_response before dispatch returns.
    None means continue. Late writes have no effect. Consumers receive copied
    headers and path parameters, not mutable framework request objects.
    """

    event_name: ClassVar[str] = "http.server.before_handler"
    operation_id: object = event_field()
    route: str = event_field()
    path_parameters: dict[str, object] = event_field()
    body_availability: HttpRequestBodyAvailability = event_field(default=HttpRequestBodyAvailability.NOT_COLLECTED)
    block_response: Optional[HttpServerBlockResponse] = event_field(default=None)
