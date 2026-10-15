from collections.abc import Callable
from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
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


class HttpRequestPhase(str, Enum):
    REQUEST_STARTED = "request_started"
    ROUTE_RESOLVED = "route_resolved"
    BEFORE_HANDLER = "before_handler"
    RESPONSE_READY = "response_ready"
    RESPONSE_COMMITTED = "response_committed"
    COMPLETED = "completed"


class HttpEnforcementAction(str, Enum):
    RESPOND = "respond"
    TERMINATE = "terminate"


@dataclass(frozen=True)
class HttpRequestData:
    """Owned HTTP values: None means unavailable; empty strings/tuples mean known empty.

    Header names are case-insensitive. Pairs preserve duplicate values and their
    order where the framework exposes them. No framework objects or body streams
    are retained. A producer must not read a body merely to populate this contract.
    """

    method: str
    url: str
    headers: Optional[tuple[tuple[str, str], ...]] = None
    query_string: Optional[str] = None
    query_parameters: Optional[tuple[tuple[str, str], ...]] = None
    route: Optional[str] = None
    body: Optional[bytes] = None


@dataclass(frozen=True)
class HttpResponseData:
    """A prepared response; the snapshot's committed flag says whether it was accepted."""

    status_code: Optional[int] = None
    headers: Optional[tuple[tuple[str, str], ...]] = None
    body: Optional[bytes] = None


@dataclass(frozen=True)
class HttpRequestError:
    """Error information without retaining exception objects, frames, or request locals."""

    type: str
    message: Optional[str]


@dataclass(frozen=True)
class HttpRequestSnapshot:
    """An immutable observation that consumers may retain after synchronous dispatch.

    The request_id is an opaque, process-local token shared by a request's snapshots;
    it is not a span identity. No errors means no error has been observed so far.
    Capabilities describe implemented actions at this phase, not framework potential.
    This data event does not accept decisions or enforce actions.
    """

    request_id: object
    framework: str
    phase: HttpRequestPhase
    request: HttpRequestData
    response: Optional[HttpResponseData] = None
    errors: tuple[HttpRequestError, ...] = ()
    handler_started: bool = False
    response_committed: bool = False
    supported_actions: frozenset[HttpEnforcementAction] = frozenset()


@dataclass
class HttpRequestDataEvent(Event):
    """Dispatch-only envelope; consumers use the immutable snapshot, not tracing state."""

    event_name = "http.server.request.data"
    snapshot: HttpRequestSnapshot


@dataclass(frozen=True)
class HttpBlockingResponse:
    """A complete replacement response; the adapter owns transport framing."""

    status_code: int = 403
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""


@dataclass(frozen=True)
class HttpInspectionDecision:
    """None means allow; response data is required only for RESPOND."""

    action: Optional[HttpEnforcementAction] = None
    response: Optional[HttpBlockingResponse] = None


class HttpDecisionAcceptance(str, Enum):
    ACCEPTED = "accepted"
    CLOSED = "closed"
    INVALID = "invalid"
    UNSUPPORTED = "unsupported"
    ALREADY_DECIDED = "already_decided"


@dataclass
class HttpRequestInspectionEvent(Event):
    """Synchronous inspection; submit is valid only on the dispatching thread.

    Acceptance acknowledges a decision, not successful response delivery. The
    adapter closes submit before resuming application or server execution.
    """

    event_name = "http.server.request.inspect"
    snapshot: HttpRequestSnapshot
    submit: Callable[[HttpInspectionDecision], HttpDecisionAcceptance]
