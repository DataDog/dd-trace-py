"""Notifications within Bottle's matched route callback lifetime."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from typing import Optional
from typing import Protocol

from ddtrace.contrib._events.web_framework import WebFrameworkRequestEvent
from ddtrace.internal.core.events import Event
from ddtrace.internal.utils import Block_config


class BottleRequest(Protocol):
    @property
    def environ(self) -> Mapping[str, Any]: ...

    @property
    def cookies(self) -> Mapping[str, str]: ...


@dataclass
class BottleRequestStartEvent(Event):
    """Request data available before invoking the route callback; the body is not consumed."""

    event_name = "bottle.request.start"
    request_event: WebFrameworkRequestEvent
    request: BottleRequest
    path_params: Mapping[str, Any]


@dataclass
class BottleResponseEvent(Event):
    """Response metadata populated before inspecting or replacing the callback outcome."""

    event_name = "bottle.request.response"
    request_event: WebFrameworkRequestEvent


@dataclass
class BottleBlockResponseEvent(Event):
    """Subscribers may supply a status, headers, and body before dispatch returns."""

    event_name = "bottle.request.block"
    block: Block_config
    response: Optional[tuple[int, Mapping[str, str], bytes]] = None
