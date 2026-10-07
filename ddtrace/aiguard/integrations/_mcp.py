"""Shared helpers for evaluating MCP tool calls detected on the model SDK side.

Model function calls carry no MCP identity, so provider listeners record the tool calls a model
returned and the conversation around them; an MCP adapter that later runs one of those calls looks
it up by call ID to evaluate it with the real ID and history. OpenAI hosted MCP approvals are
decided when the approval request is returned, and that decision is reused when the application
sends its approval back.
"""

from collections import OrderedDict
from contextvars import ContextVar
import threading
from typing import NamedTuple
from typing import Optional
import uuid

from ddtrace.aiguard._types import MCP
from ddtrace.aiguard._types import Message
from ddtrace.aiguard._types import ToolCall
from ddtrace.internal.settings.aiguard import aiguard_config
from ddtrace.internal.utils.http import canonicalize_url


def mcp_metadata(transport: str, tool_name: str, name: Optional[str] = None, url: Optional[str] = None) -> MCP:
    """Build the mcp object of a tool call; name and url are omitted when unknown, never guessed."""
    mcp = MCP(transport=transport, tool_name=tool_name)
    if name:
        mcp["name"] = name
    canonical_url = canonicalize_url(url) if url else None
    if canonical_url:
        mcp["url"] = canonical_url
    return mcp


def local_tool_call_id() -> str:
    """ID for a tool call no model issued, prefixed so it is never mistaken for a provider ID."""
    return f"dd_mcp_{uuid.uuid4().hex}"


class _ModelToolCall(NamedTuple):
    messages: list[Message]
    message_index: int


# Tool calls of the last model response evaluated in this context, keyed by call ID.
_model_tool_calls: ContextVar[Optional[dict[str, _ModelToolCall]]] = ContextVar(
    "ai_guard_model_tool_calls", default=None
)


def record_model_tool_calls(messages: list[Message], start: int) -> None:
    """Remember the tool calls in messages[start:], the model response part of a conversation."""
    if not aiguard_config._ai_guard_collect_mcp_enabled:
        return
    calls = {
        tool_call["id"]: _ModelToolCall(messages, index)
        for index in range(start, len(messages))
        for tool_call in messages[index].get("tool_calls") or []
        if tool_call.get("id")
    }
    # Replaced even when empty: a newer response supersedes the calls of an older one.
    _model_tool_calls.set(calls or None)


def tool_call_conversation(tool_call: ToolCall) -> list[Message]:
    """Return the conversation evaluated before tool_call runs.

    When the call came from a recorded model response, the real history is kept and tool_call
    replaces the model's calls in the assistant turn, since sibling calls are evaluated when they
    run. Otherwise only the tool call is sent: no history is fabricated.
    """
    calls = _model_tool_calls.get()
    model_call = calls.get(tool_call["id"]) if calls else None
    if model_call is None:
        return [Message(role="assistant", tool_calls=[tool_call])]
    messages, message_index = model_call
    assistant = messages[message_index].copy()
    assistant["tool_calls"] = [tool_call]
    return messages[:message_index] + [assistant]


class ApprovalDecision(NamedTuple):
    blocked: bool
    action: str
    reason: str
    tags: Optional[list[str]]


class _ApprovalDecisions:
    """Bounded, thread-safe map from OpenAI MCP approval request IDs to their AI Guard decision.

    Kept in process memory only: a continuation served by another process re-evaluates when the
    approval request is replayed in its input.
    """

    def __init__(self, max_size: int = 1024) -> None:
        self._max_size = max_size
        self._decisions: OrderedDict[str, ApprovalDecision] = OrderedDict()
        self._lock = threading.Lock()

    def record(self, approval_ids: list[str], decision: ApprovalDecision) -> None:
        with self._lock:
            for approval_id in approval_ids:
                self._decisions[approval_id] = decision
                self._decisions.move_to_end(approval_id)
            while len(self._decisions) > self._max_size:
                self._decisions.popitem(last=False)

    def get(self, approval_id: str) -> Optional[ApprovalDecision]:
        with self._lock:
            return self._decisions.get(approval_id)

    def clear(self) -> None:
        with self._lock:
            self._decisions.clear()


approval_decisions = _ApprovalDecisions()
