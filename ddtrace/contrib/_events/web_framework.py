from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any
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
from ddtrace.internal.utils import Block_config


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

    # Set at completion when a framework exception represents an HTTP response.
    response_from_exception: bool = event_field(default=False)

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
class WebFrameworkRequestInspectEvent(Event):
    """Request data available before invoking the route callback; the body is not consumed."""

    event_name = "web.request.inspect"
    request_event: WebFrameworkRequestEvent
    peer_ip: Optional[str]
    cookies: Mapping[str, str]
    path_params: Mapping[str, Any]


@dataclass
class WebFrameworkResponseInspectEvent(Event):
    """Response metadata populated before inspecting or replacing the callback outcome."""

    event_name = "web.response.inspect"
    request_event: WebFrameworkRequestEvent


@dataclass
class WebFrameworkBlockResponseEvent(Event):
    """Subscribers may supply a status, headers, and body before dispatch returns."""

    event_name = "web.response.block"
    block: Block_config
    response: Optional[tuple[int, Mapping[str, str], bytes]] = None
