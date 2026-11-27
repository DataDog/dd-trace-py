"""Shared tracing imports and producer-owned web framework inspection events."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Optional

from ddtrace._trace.web_framework import WebFrameworkEvents as WebFrameworkEvents
from ddtrace._trace.web_framework import WebFrameworkRequestEvent as WebFrameworkRequestEvent
from ddtrace.internal.core.events import Event
from ddtrace.internal.core.events import event_field


@dataclass
class WebFrameworkRequestInspectEvent(Event):
    """Inspect metadata before a synchronous callback, optionally supplying its response."""

    event_name = "web.request.inspect"

    request_event: WebFrameworkRequestEvent = event_field()
    # A subscriber may replace callback execution with a status, headers, and byte body.
    response: Optional[tuple[int, Mapping[str, str], bytes]] = event_field(default=None)
