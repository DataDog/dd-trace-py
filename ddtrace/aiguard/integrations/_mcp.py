"""Shared helpers for evaluating MCP tool calls detected on the model SDK side.

OpenAI hosted MCP approvals are decided when the approval request is returned, and that decision
is reused when the application sends its approval back.
"""

from collections import OrderedDict
import threading
from typing import NamedTuple
from typing import Optional

from ddtrace.aiguard._types import MCP
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
