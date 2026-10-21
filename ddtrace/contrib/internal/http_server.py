from collections.abc import Callable
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import replace
from types import TracebackType
from typing import Any
from typing import Optional
from typing import cast

from ddtrace.contrib._events.http import HTTP_REQUEST_DATA_EVENTS
from ddtrace.contrib._events.http import HttpBlockingResponse
from ddtrace.contrib._events.http import HttpRequestBlock
from ddtrace.contrib._events.http import HttpRequestBlockCheckEvent
from ddtrace.contrib._events.http import HttpRequestData
from ddtrace.contrib._events.http import HttpRequestError
from ddtrace.contrib._events.http import HttpRequestPhase
from ddtrace.contrib._events.http import HttpResponseData
from ddtrace.contrib._events.http import HttpServerRequestEvent
from ddtrace.contrib._events.http import HttpServerTracingDecisionEvent
from ddtrace.contrib.internal.trace_utils import is_tracing_enabled
from ddtrace.internal import core
from ddtrace.internal.settings.integration import IntegrationConfig


def create_http_server_context(
    *,
    framework: str,
    service: str,
    request_id: object,
    request_headers: Mapping[str, str],
    integration_config: IntegrationConfig,
    activate_distributed_headers: bool = True,
    defer_span_start: bool = False,
) -> core.ExecutionContext[HttpServerRequestEvent]:
    """Create a request context; the framework adapter owns its lifetime."""
    return core.context_with_event(
        HttpServerRequestEvent(
            service=service,
            component=integration_config.integration_name,
            request_id=request_id,
            framework=framework,
            request_headers=dict(request_headers),
            integration_config=integration_config,
            activate_distributed_headers=activate_distributed_headers,
            defer_span_start=defer_span_start,
        )
    )


_HEADER_TOKEN = frozenset("!#$%&'*+-.^_`|~0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ")


def _valid_blocking_response(value: Any) -> bool:
    if not isinstance(value, HttpBlockingResponse):
        return False
    value = cast(Any, value)
    if type(value.status_code) is not int or not 200 <= value.status_code <= 599:
        return False
    if not isinstance(value.body, bytes) or not isinstance(value.headers, tuple):
        return False
    if value.status_code in (204, 205, 304) and value.body:
        return False
    for pair in value.headers:
        if not isinstance(pair, tuple) or len(pair) != 2:
            return False
        name, header = pair
        if not isinstance(name, str) or not isinstance(header, str):
            return False
        if not name or not set(name) <= _HEADER_TOKEN:
            return False
        if any(ord(char) < 32 or ord(char) == 127 or ord(char) > 255 for char in header):
            return False
    return True


def request_error(error_type: type, error: BaseException) -> HttpRequestError:
    try:
        message: Optional[str] = str(error)
    except Exception:
        message = None
    return HttpRequestError(f"{error_type.__module__}.{error_type.__qualname__}", message)


def publish_http_event(phase: HttpRequestPhase, **fields: Any) -> None:
    event = HTTP_REQUEST_DATA_EVENTS[phase]
    if core.has_listeners(event.event_name):
        core.dispatch_event(event(phase=phase, **fields))


ExcInfo = tuple[Optional[type], Optional[BaseException], Optional[TracebackType]]
# Include every phase: a consumer may subscribe only to routing or completion.
# Tracing's own listeners can keep this conservative gate open when tracing is off.
_OBSERVER_EVENTS = (
    HttpRequestBlockCheckEvent.event_name,
    *(event.event_name for event in HTTP_REQUEST_DATA_EVENTS.values()),
)


def http_instrumentation_active() -> bool:
    """Whether anything would consume this request's lifecycle.

    False means no span would be created and no listener would be called, so a
    driver can hand the request straight to the application and skip request
    extraction entirely.
    """
    return is_tracing_enabled() or any(core.has_listeners(name) for name in _OBSERVER_EVENTS)


@dataclass(frozen=True)
class HttpLifecycleConfig:
    framework: str
    service: str
    integration_config: IntegrationConfig
    activate_distributed_headers: bool = True
    defer_span_start: bool = False


class HttpLifecycle:
    """Shared HTTP observations and decisions; transport drivers own execution."""

    def __init__(
        self,
        config: HttpLifecycleConfig,
        request_data: HttpRequestData,
        *,
        validate_block: Callable[[HttpBlockingResponse], bool] = lambda block: True,
    ) -> None:
        self.config = config
        self.request_id = object()
        self.request_data = request_data
        self.response_data: Optional[HttpResponseData] = None
        self.errors: tuple[HttpRequestError, ...] = ()
        self.handler_started = False
        self.committed = False
        self.completed = False
        self.blocked = False
        self.pending_block: Optional[HttpBlockingResponse] = None
        self.validate_block = validate_block
        self.ctx = create_http_server_context(
            framework=config.framework,
            service=config.service,
            request_id=self.request_id,
            request_headers=dict(request_data.headers or ()),
            integration_config=config.integration_config,
            activate_distributed_headers=config.activate_distributed_headers,
            defer_span_start=config.defer_span_start,
        )
        self.exc_info: ExcInfo = (None, None, None)

    def event_fields(self) -> dict[str, Any]:
        return dict(
            request_id=self.request_id,
            framework=self.config.framework,
            request=self.request_data,
            response=self.response_data,
            errors=self.errors,
            handler_started=self.handler_started,
            response_committed=self.committed,
        )

    def publish(self, phase: HttpRequestPhase) -> None:
        if core.has_listeners(HTTP_REQUEST_DATA_EVENTS[phase].event_name):
            publish_http_event(phase, **self.event_fields())

    def check_block(self, phase: HttpRequestPhase) -> Optional[HttpBlockingResponse]:
        if self.blocked or self.committed or not core.has_listeners(HttpRequestBlockCheckEvent.event_name):
            return None
        try:
            core.dispatch_event(HttpRequestBlockCheckEvent(phase=phase, **self.event_fields()))
        except HttpRequestBlock as block:
            if _valid_blocking_response(block.response) and self.validate_block(block.response):
                self.blocked = True
                self.pending_block = block.response
                return block.response
        return None

    def start(self) -> Optional[HttpBlockingResponse]:
        self.ctx.__enter__()
        self.publish(HttpRequestPhase.REQUEST_STARTED)
        return self.check_block(HttpRequestPhase.REQUEST_STARTED)

    def set_tracing_enabled(self, enabled: bool) -> None:
        """Select tracing for a deferred request without disabling HTTP consumers."""
        core.dispatch_event(
            HttpServerTracingDecisionEvent(
                request_id=self.request_id, enabled=enabled, request_headers=dict(self.request_data.headers or ())
            )
        )

    def route_resolved(self, route: str, route_pattern: str) -> Optional[HttpBlockingResponse]:
        self.request_data = replace(self.request_data, route=route, route_pattern=route_pattern)
        self.publish(HttpRequestPhase.ROUTE_RESOLVED)
        return self.check_block(HttpRequestPhase.ROUTE_RESOLVED)

    def prepare_response(self, response_data: HttpResponseData) -> Optional[HttpBlockingResponse]:
        self.response_data = response_data
        self.publish(HttpRequestPhase.RESPONSE_READY)
        self.check_block(HttpRequestPhase.RESPONSE_READY)
        # Framework hooks may have replaced the response after an early block.
        return self.pending_block

    def delivery_attempted(self, response_data: HttpResponseData) -> None:
        self.response_data = response_data
        # Consume before delivery: a failed attempt may be retried with an error response.
        self.pending_block = None

    def response_committed(self) -> None:
        self.committed = True
        self.publish(HttpRequestPhase.RESPONSE_COMMITTED)

    def before_handler(self) -> Optional[HttpBlockingResponse]:
        self.publish(HttpRequestPhase.BEFORE_HANDLER)
        blocked = self.check_block(HttpRequestPhase.BEFORE_HANDLER)
        if blocked is not None:
            return blocked
        self.handler_started = True
        return None

    def record_exception(self, exc_info: ExcInfo) -> None:
        if not self.completed and exc_info[0] is not None:
            self.exc_info = exc_info
            self.errors = (request_error(exc_info[0], cast(BaseException, exc_info[1])),)

    def finish(self) -> None:
        if self.completed:
            return
        self.completed = True
        try:
            self.publish(HttpRequestPhase.COMPLETED)
        finally:
            try:
                self.ctx.__exit__(*self.exc_info)
            finally:
                self.exc_info = (None, None, None)
