from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.contrib._events.http import HttpRequestBaseEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
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
class WebFrameworkCallbackStartedEvent(Event):
    """A plugin callback boundary, independent of tracing and transport completion.

    Treat fields as read-only. Headers are copied; bodies and live framework objects
    are excluded. callback_id identifies this invocation, including nested calls.
    """

    event_name = "web.framework.callback.started"

    callback_id: object = event_field()
    framework: str = event_field()
    request_method: str = event_field()
    request_url: str = event_field()
    request_route: str = event_field()
    request_headers: tuple[tuple[str, str], ...] = event_field()
    query: str = event_field()


@dataclass
class WebFrameworkCallbackCompletedEvent(WebFrameworkCallbackStartedEvent):
    """The callback boundary exited, before response iteration and transport delivery.

    Completion is dispatched before the associated tracing context exits, if any.
    Errors are descriptions and retain neither exception objects nor tracebacks.
    """

    event_name = "web.framework.callback.completed"

    response_status_code: int = event_field()
    response_headers: tuple[tuple[str, str], ...] = event_field()
    error_type: Optional[str] = event_field(default=None)
    error_message: Optional[str] = event_field(default=None)
