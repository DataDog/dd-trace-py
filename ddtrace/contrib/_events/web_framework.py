from dataclasses import dataclass
from enum import Enum
from typing import Optional

from ddtrace._trace.events import TracingEvent
from ddtrace.contrib._events.http import HttpRequestBaseEvent
from ddtrace.ext import SpanKind
from ddtrace.ext import SpanTypes
from ddtrace.internal.core.events import event_field
from ddtrace.internal.schema import SpanDirection
from ddtrace.internal.schema import schematize_url_operation


class WebFrameworkEvents(str, Enum):
    WEB_REQUEST = "web.request"


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

    def __post_init__(self):
        self.operation_name = schematize_url_operation(
            self.http_operation, protocol="http", direction=SpanDirection.INBOUND
        )
