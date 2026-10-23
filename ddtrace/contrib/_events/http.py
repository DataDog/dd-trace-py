from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Callable
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


Headers = tuple[tuple[str, str], ...]


class HttpRequestPhase(str, Enum):
    ROUTE_RESOLVED = "route_resolved"
    COMPLETED = "completed"
    # Add other phases when their timing and guarantees are defined.


@dataclass(frozen=True)
class HttpRequestData:
    method: Optional[str] = None
    url: Optional[str] = None
    headers: Optional[Headers] = None
    query: Optional[str] = None
    route: Optional[str] = None


@dataclass(frozen=True)
class HttpResponseData:
    status_code: Optional[int] = None
    headers: Optional[Headers] = None


@dataclass(frozen=True)
class HttpErrorData:
    type_name: str
    message: Optional[str] = None


@dataclass(frozen=True)
class HttpRequestSnapshot:
    """Immutable HTTP data captured at a defined request phase."""

    request_id: str
    phase: HttpRequestPhase
    request: HttpRequestData
    response: Optional[HttpResponseData] = None
    errors: Optional[tuple[HttpErrorData, ...]] = None


@dataclass
class HttpRequestDataEvent(Event):
    """Share a request snapshot with APM, AppSec, and other HTTP observers."""

    event_name = "http.request.data"
    snapshot: HttpRequestSnapshot


class HttpEnforcementAction(str, Enum):
    RESPOND = "respond"


@dataclass(frozen=True)
class HttpBlockingResponse:
    status_code: int
    headers: Headers = ()
    body: bytes = b""


@dataclass(frozen=True)
class HttpInspectionDecision:
    action: HttpEnforcementAction
    response: Optional[HttpBlockingResponse] = None


class HttpDecisionAcceptance(str, Enum):
    ACCEPTED = "accepted"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"
    ALREADY_DECIDED = "already_decided"
    CLOSED = "closed"


@dataclass
class HttpRequestInspectionEvent(Event):
    """Offer a request snapshot and decision callback to HTTP inspection consumers."""

    event_name = "http.request.inspect"
    snapshot: HttpRequestSnapshot
    supported_actions: frozenset[HttpEnforcementAction]
    submit: Callable[[HttpInspectionDecision], HttpDecisionAcceptance]
