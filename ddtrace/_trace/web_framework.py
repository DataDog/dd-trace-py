from dataclasses import dataclass
from enum import Enum
from typing import ClassVar
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import WEB_REQUEST_STARTING_EVENT
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field
from ddtrace.internal.core.http import HttpRequestBaseEvent
from ddtrace.internal.schema import SpanDirection
from ddtrace.internal.schema import schematize_url_operation


class WebFrameworkEvents(str, Enum):
    WEB_REQUEST = "web.request"
    WEB_REQUEST_STARTING = WEB_REQUEST_STARTING_EVENT
    WSGI_REQUEST = "web.request.wsgi"
    WSGI_RESPONSE = "web.response.wsgi"


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

    # Address of the connected peer, reported as the network peer IP.
    peer_ip: Optional[str] = event_field(default=None)

    # Tag the request headers and the peer IP when the span starts, so listeners see them while the handler runs.
    tag_request_on_start: bool = event_field(default=False)

    # Whether completion replays request attributes and response headers. Integrations that
    # tag them as they happen disable it so later overrides and redactions are not replaced.
    tag_request_on_end: ClassVar[bool] = True

    def __post_init__(self) -> None:
        self.operation_name = schematize_url_operation(
            self.http_operation, protocol="http", direction=SpanDirection.INBOUND
        )


@dataclass
class WSGIRequestEvent(WebFrameworkRequestEvent):
    """A WSGI request whose context ends after its response iterable completes."""

    event_name = WebFrameworkEvents.WSGI_REQUEST.value

    tag_request_on_end = False


@dataclass
class WSGIResponseEvent(Event):
    """A WSGI response iterable returned by the application, nested in its request context."""

    event_name = WebFrameworkEvents.WSGI_RESPONSE.value

    component: str = event_field()
    operation_name: str = event_field()
