from collections.abc import Mapping
from collections.abc import MutableMapping
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field
from ddtrace.internal.schema import SpanDirection
from ddtrace.internal.schema import schematize_url_operation


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
    # The matched route as the application declared it, without any mount prefix.
    # route carries the prefix; the two differ only for a mounted application.
    route_pattern: Optional[str] = None


@dataclass(frozen=True)
class HttpResponseData:
    """A prepared response; the event's committed flag says whether it was accepted."""

    status_code: Optional[int] = None
    headers: Optional[tuple[tuple[str, str], ...]] = None
    body: Optional[bytes] = None


@dataclass(frozen=True)
class HttpRequestError:
    """Error information without retaining exception objects, frames, or request locals."""

    type: str
    message: Optional[str]


@dataclass
class HttpServerRequestEvent(TracingEvent):
    """Opens the context that a server request span is scoped to.

    Every HTTP server integration dispatches this same event; framework identifies
    which one, so a consumer that needs framework-specific conventions selects on
    that field rather than on a per-framework event name.

    Carries only what is knowable before the handler runs; nothing writes to it
    afterwards. Later HTTP observations and an optional tracing-policy decision
    are separate events, tied back to this context by request_id rather than by
    whichever context happens to be active when that event is dispatched.
    """

    event_name = "http.server.request"

    span_type = SpanTypes.WEB
    span_kind = SpanKind.SERVER

    # Shared with every HttpRequestDataEvent for this request. Opaque and
    # process-local: identity is the only thing a consumer may rely on.
    request_id: object = event_field()
    framework: str = event_field()
    request_headers: Mapping[str, str] = event_field(default_factory=dict)
    defer_span_start: bool = event_field(default=False)

    def __post_init__(self) -> None:
        # The schema implementation is selected from a heterogeneous function table.
        self.operation_name = schematize_url_operation(  # type: ignore[operator]
            f"{self.framework}.request", protocol="http", direction=SpanDirection.INBOUND
        )


@dataclass
class HttpServerTracingDecisionEvent(Event):
    """Framework tracing policy, separate from HTTP observations and block checks.

    Deferred requests select their policy before publishing the resolved route.
    The first decision wins; it cannot suppress a span that has already started.
    """

    event_name = "http.server.request.tracing_decision"
    request_id: object = event_field()
    enabled: bool = event_field()


@dataclass
class HttpRequestDataEvent(Event):
    """HTTP observations delivered under phase-specific event names.

    Consumers must treat events as read-only. Each dispatch has a fresh envelope;
    request, response, and error values are immutable and may be retained. The
    request_id is an opaque process-local token, independent of span identity.
    Listener return values do not affect request handling.
    """

    request_id: object = event_field()
    framework: str = event_field()
    phase: HttpRequestPhase = event_field()
    request: HttpRequestData = event_field()
    response: Optional[HttpResponseData] = event_field(default=None)
    errors: tuple[HttpRequestError, ...] = event_field(default=())
    handler_started: bool = event_field(default=False)
    response_committed: bool = event_field(default=False)


@dataclass
class HttpRequestStartedEvent(HttpRequestDataEvent):
    event_name = "http.server.request.started"


@dataclass
class HttpRouteResolvedEvent(HttpRequestDataEvent):
    event_name = "http.server.request.route_resolved"


@dataclass
class HttpBeforeHandlerEvent(HttpRequestDataEvent):
    event_name = "http.server.request.before_handler"


@dataclass
class HttpResponseReadyEvent(HttpRequestDataEvent):
    event_name = "http.server.request.response_ready"


@dataclass
class HttpResponseCommittedEvent(HttpRequestDataEvent):
    event_name = "http.server.request.response_committed"


@dataclass
class HttpRequestCompletedEvent(HttpRequestDataEvent):
    event_name = "http.server.request.completed"


HTTP_REQUEST_DATA_EVENTS: Mapping[HttpRequestPhase, type[HttpRequestDataEvent]] = {
    HttpRequestPhase.REQUEST_STARTED: HttpRequestStartedEvent,
    HttpRequestPhase.ROUTE_RESOLVED: HttpRouteResolvedEvent,
    HttpRequestPhase.BEFORE_HANDLER: HttpBeforeHandlerEvent,
    HttpRequestPhase.RESPONSE_READY: HttpResponseReadyEvent,
    HttpRequestPhase.RESPONSE_COMMITTED: HttpResponseCommittedEvent,
    HttpRequestPhase.COMPLETED: HttpRequestCompletedEvent,
}


@dataclass(frozen=True)
class HttpBlockingResponse:
    """A complete replacement response; the adapter owns transport framing."""

    status_code: int = 403
    headers: tuple[tuple[str, str], ...] = ()
    body: bytes = b""


class HttpRequestBlock(BaseException):
    """Interrupt a synchronous block check with a replacement response.

    BaseException deliberately bypasses the event hub's ordinary listener-error
    isolation. Raise only from a HttpRequestBlockCheckEvent listener. The adapter
    validates the response and owns transport framing and request completion.
    """

    def __init__(
        self,
        status_code: int = 403,
        headers: tuple[tuple[str, str], ...] = (),
        body: bytes = b"",
    ) -> None:
        super().__init__(status_code)
        self.response = HttpBlockingResponse(status_code=status_code, headers=headers, body=body)


@dataclass
class HttpRequestBlockCheckEvent(HttpRequestDataEvent):
    """A synchronous opportunity to raise HttpRequestBlock; first block wins.

    Only dispatched where the adapter can replace a response. Returning permits
    the next listener to run. Retaining this event grants no later control.
    """

    event_name = "http.server.request.block_check"
