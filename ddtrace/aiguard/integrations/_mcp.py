"""MCP helpers for model SDK integrations: mcp_servers entries and their server_id fingerprint,
plus the decisions taken on OpenAI hosted MCP approval requests when they were returned.
"""

from collections import OrderedDict
from collections.abc import Sequence
import hashlib
from typing import NamedTuple
from typing import Optional

from ddtrace.aiguard._types import MCP
from ddtrace.aiguard._types import MCPManagedBy
from ddtrace.aiguard._types import MCPServer
from ddtrace.aiguard._types import MCPTransport
from ddtrace.aiguard._types import Message
from ddtrace.internal import forksafe
from ddtrace.internal.utils.http import url_origin


def mcp_server_id(
    url: Optional[str] = None,
    connector_id: Optional[str] = None,
    command: Optional[str] = None,
    args: Sequence[str] = (),
    name: Optional[str] = None,
) -> Optional[str]:
    """Deterministic server ID from the first known key among url, connector, stdio command and name.

    The url key is its origin; transport, session and item IDs are left out so the ID stays stable.
    """
    origin = url_origin(url) if url else None
    if origin:
        key = f"url|{origin}"
    elif connector_id:
        key = f"connector|{connector_id}"
    elif command:
        key = "|".join(("stdio", command, *args))
    elif name:
        key = f"name|{name}"
    else:
        return None
    return "mcp_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def mcp_server(
    managed_by: MCPManagedBy,
    transport: MCPTransport,
    name: Optional[str] = None,
    url: Optional[str] = None,
    connector_id: Optional[str] = None,
) -> Optional[MCPServer]:
    """Build an mcp_servers entry with no tools yet; url is reported as its origin only."""
    url = url_origin(url) if url else None
    server_id = mcp_server_id(url=url, connector_id=connector_id, name=name)
    if server_id is None:
        return None
    server = MCPServer(id=server_id, managed_by=managed_by, transport=transport, tools={})
    if name:
        server["name"] = name
    if url:
        server["url"] = url
    return server


def mcp_reference(server: MCPServer, tool_name: str, original_name: str) -> MCP:
    """Record tool_name as a tool of server and return the mcp object of its tool call."""
    server["tools"][tool_name] = original_name
    return MCP(server_id=server["id"])


def referenced_mcp_servers(messages: list[Message], servers: Sequence[MCPServer]) -> Optional[list[MCPServer]]:
    """The servers the tool calls of messages reference, with only the tools they call; None if none.

    Restricting to referenced servers keeps every entry resolvable and every tools map non-empty.
    """
    by_id = {server["id"]: server for server in servers}
    called: dict[str, dict[str, str]] = {}
    for message in messages:
        for tool_call in message.get("tool_calls") or []:
            mcp = tool_call.get("mcp")
            server = by_id.get(mcp["server_id"]) if mcp else None
            name = tool_call["function"]["name"]
            if server is not None and name in server["tools"]:
                called.setdefault(server["id"], {})[name] = server["tools"][name]
    if not called:
        return None
    result = []
    for server_id, tools in called.items():
        server = by_id[server_id].copy()
        server["tools"] = tools
        result.append(server)
    return result


class ApprovalDecision(NamedTuple):
    blocked: bool
    action: str
    reason: str
    tags: Optional[list[str]]


class _ApprovalDecisions:
    """Bounded, thread-safe map from OpenAI MCP approval request IDs to their AI Guard decision.

    A miss (another process, eviction, failed evaluation) lets an unreplayed approval through fail-open.
    """

    def __init__(self, max_size: int = 1024) -> None:
        self._max_size = max_size
        self._decisions: OrderedDict[str, ApprovalDecision] = OrderedDict()
        self._lock = forksafe.Lock()

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
