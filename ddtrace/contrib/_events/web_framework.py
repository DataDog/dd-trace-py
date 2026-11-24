"""Shared inspection events and compatibility imports for tracing contracts."""

from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace._trace.events import WebFrameworkResponseEvent as WebFrameworkResponseEvent
from ddtrace._trace.web_framework import WebFrameworkEvents as WebFrameworkEvents
from ddtrace._trace.web_framework import WebFrameworkRequestEvent as WebFrameworkRequestEvent
from ddtrace.internal.core.events import Event
from ddtrace.internal.utils import Block_config


@dataclass
class WebFrameworkRequestInspectEvent(Event):
    """Request data available before invoking the route callback; the body is not consumed."""

    event_name = "web.request.inspect"
    request_event: WebFrameworkRequestEvent
    peer_ip: Optional[str]
    cookies: Mapping[str, str]
    path_params: Mapping[str, Any]
    # Restore product state when the adapter's replacement window closes.
    cleanup_callbacks: list[Callable[[], None]] = field(default_factory=list)


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
