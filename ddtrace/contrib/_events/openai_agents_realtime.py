"""Product observer handoff for an Agents Realtime WebSocket connection."""

from contextvars import Context
from dataclasses import dataclass
from typing import Any
from typing import Callable
from typing import Optional
from typing import Protocol

from ddtrace.internal.core.events import Event


class AgentsRealtimeSessionObserver(Protocol):
    context: Context
    handling: int
    _closed: bool

    def observe_server(self, event: Any) -> bool: ...
    def prepare_client_event(self, event: Any) -> Callable[[bool], None]: ...
    def after_handler(self) -> None: ...
    def finish_session(self) -> None: ...


@dataclass
class OpenAIAgentsRealtimeSessionEvent(Event):
    event_name = "openai_agents.realtime.session"
    owner: Any
    url: str
    model: Optional[str]
    observer: Optional[AgentsRealtimeSessionObserver] = None
