from dataclasses import dataclass
from enum import Enum
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.contrib._events.http import HttpRequestBaseEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.constants import WEB_REQUEST_STARTING_EVENT
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field
from ddtrace.internal.schema import SpanDirection
from ddtrace.internal.schema import schematize_url_operation


class WebFrameworkEvents(str, Enum):
    WEB_REQUEST = "web.request"
    WEB_REQUEST_STARTING = WEB_REQUEST_STARTING_EVENT


@dataclass
class WebFrameworkRequestEvent(HttpRequestBaseEvent, TracingEvent):
    event_name = WebFrameworkEvents.WEB_REQUEST.value

    span_type = SpanTypes.WEB
    span_kind = SpanKind.SERVER

    # Whether the integration can emit its complete lifecycle without a tracing span.
    supports_spanless_lifecycle: bool = event_field(default=False)

    # Whether header keys should preserve their original casing during extraction.
    headers_case_sensitive: bool = event_field()

    # Optional per-request override for distributed tracing header activation.
    distributed_headers_config_override: Optional[bool] = event_field(default=None)

    # Allow fallback resource naming (for example "METHOD STATUS") when no route/resource is set.
    allow_default_resource: bool = event_field(default=False)

    # Internal flag indicating fallback resource naming should be applied at finish.
    set_resource: bool = event_field(default=False)

    # Framework-resolved route template/path used for http.route and resource enrichment.
    request_route: Optional[str] = event_field(default=None)

    # Optional per-request override for query string tagging.
    # aiohttp supports app-level trace_query_string that can differ from integration_config.
    trace_query_string: Optional[bool] = event_field(default=None)

    # Use to add additional tags after the span was created
    additional_tags: dict[str, str] = event_field(default_factory=dict)

    def __post_init__(self):
        self.operation_name = schematize_url_operation(
            self.http_operation, protocol="http", direction=SpanDirection.INBOUND
        )


@dataclass
class WebFrameworkRequestExceptionEvent(Event):
    """Synchronous exception notification; consumers must not retain its traceback."""

    event_name = "web.request.exception"

    request_context: core.ExecutionContext[WebFrameworkRequestEvent]
    request_event: WebFrameworkRequestEvent
    exc_info: ExcInfoType


@dataclass
class WebFrameworkResponseEvent(Event):
    """A deferred response is ready for iteration after its application returns."""

    event_name = "web.response"

    request_context: core.ExecutionContext[WebFrameworkRequestEvent]
    operation_name: str
    response: Any
    span_modifier: Callable[[Any, Any], None]
