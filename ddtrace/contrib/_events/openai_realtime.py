"""Observer handoff for an OpenAI Realtime connection's bidirectional event stream."""

from dataclasses import dataclass
from typing import Any
from typing import Optional
from typing import Protocol

from ddtrace.internal.core.events import Event


class RealtimeSessionObserver(Protocol):
    def on_client_event(self, event: Any) -> None: ...
    def on_server_event(self, event: Any) -> None: ...
    def finish_session(self) -> None: ...


@dataclass
class OpenAIRealtimeSessionEvent(Event):
    """Dispatched when a Realtime connection opens. A product that observes the session sets observer, which
    then receives every event the connection sends or parses, and is finished once the connection closes.
    """

    event_name = "openai.realtime.session"
    client: Any
    model: Optional[str]
    observer: Optional[RealtimeSessionObserver] = None
