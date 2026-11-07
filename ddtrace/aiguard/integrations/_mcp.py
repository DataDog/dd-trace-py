"""MCP helpers for model SDK integrations: mcp_servers entries and their server_id fingerprint,
the decisions taken on OpenAI hosted MCP approval requests, and the MCP SDK client transport capture.
"""

from collections import OrderedDict
from collections.abc import Sequence
import hashlib
import os.path
import re
from types import ModuleType
from typing import Any
from typing import NamedTuple
from typing import Optional
from weakref import WeakKeyDictionary

from ddtrace.aiguard._types import MCP
from ddtrace.aiguard._types import Action
from ddtrace.aiguard._types import MCPManagedBy
from ddtrace.aiguard._types import MCPServer
from ddtrace.aiguard._types import MCPTransport
from ddtrace.aiguard._types import Message
from ddtrace.internal import forksafe
import ddtrace.internal.logger as ddlogger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.utils.http import url_origin
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


logger = ddlogger.get_logger(__name__)


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
    command: Optional[str] = None,
    args: Sequence[str] = (),
) -> Optional[MCPServer]:
    """Build an mcp_servers entry with no tools yet; url is reported as its origin only.

    args must only hold safe identifiers (see safe_stdio_args); they are ignored without command.
    """
    url = url_origin(url) if url else None
    args = list(args) if command else []
    server_id = mcp_server_id(url=url, connector_id=connector_id, command=command, args=args, name=name)
    if server_id is None:
        return None
    server = MCPServer(id=server_id, managed_by=managed_by, transport=transport, tools={})
    if name:
        server["name"] = name
    if url:
        server["url"] = url
    if command:
        server["command"] = command
        if args:
            server["args"] = args
    return server


# Package runners whose first positional argument is the package to run, and their flags with no value.
_PACKAGE_RUNNERS = frozenset(("npx", "bunx", "pnpx", "uvx"))
_RUNNER_FLAGS = frozenset(("-y", "--yes", "-q", "--quiet"))
_PACKAGE_ID = re.compile(r"^(@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*(@[a-z0-9][a-z0-9._+-]*)?$", re.IGNORECASE)
_PYTHON = re.compile(r"^python(\d+(\.\d+)?)?$")
_PYTHON_MODULE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def safe_stdio_args(command: str, args: Sequence[str]) -> list[str]:
    """The package or module a stdio launcher runs (npx/bunx/pnpx/uvx <package>, python -m <module>).

    Anything else returns no args: arbitrary arguments can hold paths, tokens or other secrets.
    """
    program = os.path.basename(command).lower()
    for suffix in (".exe", ".cmd"):
        if program.endswith(suffix):
            program = program[: -len(suffix)]
    if program in _PACKAGE_RUNNERS:
        for arg in args:
            if arg in _RUNNER_FLAGS:
                continue
            return [arg] if isinstance(arg, str) and _PACKAGE_ID.match(arg) else []
    elif _PYTHON.match(program) and len(args) >= 2 and args[0] == "-m":
        module = args[1]
        return [module] if isinstance(module, str) and _PYTHON_MODULE.match(module) else []
    return []


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
    action: Action
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


# MCP SDK client transports by module. Their asynccontextmanager functions are wrapped read-only in
# place, so references imported before the wrap are covered too, to record the server their streams reach.
_TRANSPORTS = {
    "mcp.client.stdio": ("stdio_client",),
    "mcp.client.sse": ("sse_client",),
    "mcp.client.streamable_http": ("streamable_http_client", "streamablehttp_client"),
}
# Server of the read stream a transport yielded, which the ClientSession built on it keeps.
_servers_by_stream: "WeakKeyDictionary[Any, MCPServer]" = WeakKeyDictionary()
_wrapped_transports: list[tuple[Any, Any]] = []


def _stream_key(stream: Any) -> Any:
    # mcp>=2 HTTP transports yield slotted wrappers that cannot be weakly referenced; key on their inner stream.
    return getattr(stream, "_inner", stream)


def _transport_server(transport: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[MCPServer]:
    """Server of a transport call: the stdio command and safe args, or the URL origin. Never headers or env."""
    if transport == "stdio_client":
        params = args[0] if args else kwargs.get("server")
        command = getattr(params, "command", None)
        if not isinstance(command, str) or not command:
            return None
        stdio_args = getattr(params, "args", None) or []
        return mcp_server("client", "stdio", command=command, args=safe_stdio_args(command, stdio_args))
    url = args[0] if args else kwargs.get("url")
    if not isinstance(url, str):
        return None
    return mcp_server("client", "sse" if transport == "sse_client" else "streamable_http", url=url)


class _StreamsRecorder:
    """Async generator proxy that records the server of the first streams a transport yields."""

    __slots__ = ("_agen", "_server")

    def __init__(self, agen: Any, server: MCPServer) -> None:
        self._agen = agen
        self._server = server

    async def __anext__(self) -> Any:
        streams = await self._agen.__anext__()
        try:
            _servers_by_stream[_stream_key(streams[0])] = self._server
        except Exception:
            logger.debug("AI Guard MCP: failed to record the transport streams", exc_info=True)
        return streams

    def asend(self, value: Any) -> Any:
        return self._agen.asend(value)

    def athrow(self, *args: Any) -> Any:
        return self._agen.athrow(*args)

    def aclose(self) -> Any:
        return self._agen.aclose()


def _transport_capture(transport: str) -> Any:
    def capture(f: Any, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Any:
        context_manager = f(*args, **kwargs)
        try:
            server = _transport_server(transport, args, kwargs)
            if server is not None:
                # The generator asynccontextmanager drives; the transport itself runs unchanged.
                context_manager.gen = _StreamsRecorder(context_manager.gen, server)
        except Exception:
            logger.debug("AI Guard MCP: failed to capture the %s parameters", transport, exc_info=True)
        return context_manager

    return capture


_CAPTURES = {
    transport: _transport_capture(transport) for transports in _TRANSPORTS.values() for transport in transports
}


def _wrap_transports(module: ModuleType) -> None:
    for attr in _TRANSPORTS.get(module.__name__, ()):
        try:
            function, capture = getattr(module, attr), _CAPTURES[attr]
            if not is_wrapped_with(function, capture):
                wrap(function, capture)
                _wrapped_transports.append((function, capture))
        except Exception:
            logger.debug("AI Guard MCP: failed to wrap %s.%s", module.__name__, attr, exc_info=True)


def capture_mcp_transports() -> None:
    """Record the server reached by the MCP SDK client transports when their modules are imported."""
    for module in _TRANSPORTS:
        ModuleWatchdog.register_module_hook(module, _wrap_transports)


def release_mcp_transports() -> None:
    for module in _TRANSPORTS:
        ModuleWatchdog.unregister_module_hook(module, _wrap_transports)
    while _wrapped_transports:
        function, capture = _wrapped_transports.pop()
        try:
            unwrap(function, capture)
        except Exception:
            logger.debug("AI Guard MCP: failed to unwrap %r", function, exc_info=True)


def client_session_server(session: Any) -> Optional[MCPServer]:
    """Server of an MCP ClientSession, from the transport that produced its read stream; None if unknown."""
    stream = getattr(session, "_read_stream", None)
    if stream is None:
        # mcp>=2 sessions keep their streams on their dispatcher.
        stream = getattr(getattr(session, "_dispatcher", None), "_read_stream", None)
    if stream is None:
        return None
    try:
        return _servers_by_stream.get(_stream_key(stream))
    except TypeError:
        return None
