"""Product-neutral state interface for the ElevenLabs transport wrappers."""

from contextvars import ContextVar
from typing import Any
from typing import ContextManager
from typing import Optional
from typing import Protocol


class ConversationState(Protocol):
    closed: bool
    closing: bool
    error: bool

    def on_send(self, raw: Any) -> None: ...
    def on_receive(self, raw: Any) -> None: ...
    def close(self) -> None: ...
    def begin_tool(self, call_id: str, name: str, parameters: dict[str, Any]) -> Optional[Any]: ...
    def end_tool(self, call_id: str, result: Any = None, error: bool = False) -> None: ...
    def activate_tool(self, span: Any, context_provider: Any) -> ContextManager[Any]: ...


_CURRENT_STATE: ContextVar[Optional[ConversationState]] = ContextVar("elevenlabs_conversation_state", default=None)
