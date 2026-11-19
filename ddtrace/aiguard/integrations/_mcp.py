"""MCP helpers for model SDK integrations: mcp_servers entries and their server_id fingerprint,
the decisions taken on OpenAI hosted MCP approval requests, and the MCP SDK client transport capture.
"""

from collections import OrderedDict
from collections.abc import Sequence
import enum
import hashlib
import ipaddress
import os.path
import re
from types import ModuleType
from typing import Any
from typing import Literal
from typing import NamedTuple
from typing import Optional
from typing import Union
import unicodedata
from urllib import parse
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
from ddtrace.internal.native import uuid4
from ddtrace.internal.utils.http import url_origin
from ddtrace.internal.wrapping import is_wrapped_with
from ddtrace.internal.wrapping import unwrap
from ddtrace.internal.wrapping import wrap


logger = ddlogger.get_logger(__name__)


# Schemes whose host the WHATWG URL Standard parses as a domain or an IP address.
_SPECIAL_SCHEMES = frozenset(("http", "https", "ws", "wss", "ftp", "file"))
# The full stops that IDNA treats as label separators.
# The only non-ASCII host characters that are mapped: the WHATWG mapping of every other one cannot be
# reproduced exactly here (for example IDNA deviations such as ß), so such a host gets no url key.
_MAPPED_TO_DOT = frozenset("\u3002\uff0e\uff61")
_SOFT_HYPHEN = "\u00ad"
# Characters that make the WHATWG host parser fail, after percent-decoding.
_FORBIDDEN_HOST_CHARS = frozenset("\x00\t\n\r #%/:<>?@[\\]^|\x7f") | frozenset(map(chr, range(0x20)))


def mcp_url_origin(url: str) -> Optional[str]:
    """Origin of a server URL for mcp_servers and the url key of the server ID; None to use the next key.

    All tracers compute the same ID, so the host follows the WHATWG URL Standard: it is
    percent-decoded, an IPv4 literal in any number form becomes its dotted quad and an IPv6 literal
    its compressed form. Fullwidth ASCII forms, the ideographic full stops and the soft hyphen are mapped
    to ASCII. Any other non-ASCII host, a host with an IDN (xn--) label and a host that the WHATWG
    parser rejects give None.
    """
    origin = url_origin(url)
    if origin is None:
        return None
    scheme, _, netloc = origin.partition("://")
    if scheme not in _SPECIAL_SCHEMES:
        return origin
    # url_origin already lowercased the host, dropped a default port and bracketed an IPv6 host.
    host: Optional[str]
    if netloc.startswith("["):
        raw_host, _, rest = netloc[1:].partition("]")
        host = _canonical_ipv6(raw_host)
        port = rest[1:]
    else:
        raw_host, separator, port = netloc.rpartition(":")
        if not separator:
            raw_host, port = netloc, ""
        host = _canonical_domain(raw_host)
    if host is None:
        return None
    return f"{scheme}://{host}:{port}" if port else f"{scheme}://{host}"


def _canonical_domain(host: str) -> Optional[str]:
    host = parse.unquote(host)
    if not host.isascii():
        host = _ascii_mapped(host)
    if not host or any(char in _FORBIDDEN_HOST_CHARS for char in host):
        return None
    host = host.lower()
    labels = host.split(".")
    if any(label.startswith("xn--") for label in labels):
        return None
    # The WHATWG "ends in a number" rule: a numeric last label makes the whole host an IPv4 address.
    if labels[-1] == "" and len(labels) > 1:
        labels = labels[:-1]
    if _is_ipv4_number(labels[-1]):
        return _whatwg_ipv4(labels)
    return host


def _ascii_mapped(host: str) -> str:
    """The ASCII host that the WHATWG domain mapping gives for fullwidth ASCII forms, the ideographic
    full stops and the soft hyphen; an empty string for any other non-ASCII host, which has no url key.
    """
    mapped = []
    for char in host:
        if char == _SOFT_HYPHEN:
            continue
        if char in _MAPPED_TO_DOT:
            mapped.append(".")
        elif "\uff01" <= char <= "\uff5e":
            mapped.append(unicodedata.normalize("NFKC", char))
        elif char.isascii():
            mapped.append(char)
        else:
            return ""
    return "".join(mapped)


def _is_ipv4_number(label: str) -> bool:
    if label.isdigit():
        return True
    return label[:2] == "0x" and all(char in "0123456789abcdef" for char in label[2:])


def _parse_ipv4_number(part: str) -> Optional[int]:
    if not part:
        return None
    base = 10
    if part[:2] == "0x":
        base, part = 16, part[2:]
    elif len(part) > 1 and part[0] == "0":
        base, part = 8, part[1:]
    if not part:
        return 0
    # int() also accepts signs and underscores, which the WHATWG IPv4 parser rejects.
    if any(char not in "0123456789abcdef"[:base] for char in part):
        return None
    try:
        return int(part, base)
    except ValueError:
        return None


def _whatwg_ipv4(labels: list[str]) -> Optional[str]:
    if len(labels) > 4:
        return None
    numbers = [_parse_ipv4_number(label) for label in labels]
    if any(number is None for number in numbers):
        return None
    values: list[int] = numbers  # type: ignore[assignment]
    if any(number > 255 for number in values[:-1]) or values[-1] >= 256 ** (5 - len(values)):
        return None
    address = values[-1]
    for index, number in enumerate(values[:-1]):
        address += number * 256 ** (3 - index)
    return ".".join(str((address >> shift) & 0xFF) for shift in (24, 16, 8, 0))


def _canonical_ipv6(host: str) -> Optional[str]:
    """WHATWG IPv6 serialization: lowercase hex, longest run of two or more zero pieces compressed, no zone."""
    if "%" in host:
        return None
    try:
        packed = ipaddress.IPv6Address(host).packed
    except ValueError:
        return None
    pieces = [int.from_bytes(packed[i : i + 2], "big") for i in range(0, 16, 2)]
    start, length = -1, 1
    index = 0
    while index < 8:
        if pieces[index] == 0:
            end = index
            while end < 8 and pieces[end] == 0:
                end += 1
            if end - index > length:
                start, length = index, end - index
            index = end
        else:
            index += 1
    if start < 0:
        return "[" + ":".join(f"{piece:x}" for piece in pieces) + "]"
    head = ":".join(f"{piece:x}" for piece in pieces[:start])
    tail = ":".join(f"{piece:x}" for piece in pieces[start + length :])
    return f"[{head}::{tail}]"


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
    origin = mcp_url_origin(url) if url else None
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
    url = mcp_url_origin(url) if url else None
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

    Servers sharing an ID become one entry: the first server's fields, with the tools of all of them.
    """
    by_id: dict[str, MCPServer] = {}
    tools_by_id: dict[str, dict[str, str]] = {}
    for server in servers:
        by_id.setdefault(server["id"], server)
        known = tools_by_id.setdefault(server["id"], {})
        for name, original_name in server["tools"].items():
            known.setdefault(name, original_name)
    called: dict[str, dict[str, str]] = {}
    for message in messages:
        for tool_call in message.get("tool_calls") or []:
            mcp = tool_call.get("mcp")
            server_id = mcp["server_id"] if mcp else None
            tools = tools_by_id.get(server_id) if server_id else None
            name = tool_call["function"]["name"]
            if server_id and tools is not None and name in tools:
                called.setdefault(server_id, {})[name] = tools[name]
    if not called:
        return None
    result = []
    for server_id, tools in called.items():
        server = by_id[server_id].copy()
        server["tools"] = tools
        result.append(server)
    return result


def local_tool_call_id() -> str:
    """ID for a tool call no model issued, prefixed so it is never mistaken for a provider ID."""
    return f"dd_mcp_{uuid4().replace('-', '')}"


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


class _Unidentified(enum.Enum):
    SERVER = 0


# A server that a known transport reached, but that no ID key identifies (for example an IDN host). Its
# tool calls still get the check, as ordinary tool calls without MCP metadata.
UNIDENTIFIED_SERVER: Literal[_Unidentified.SERVER] = _Unidentified.SERVER
SessionServer = Union[MCPServer, Literal[_Unidentified.SERVER]]

# Server of the read stream a transport yielded, which the ClientSession built on it keeps.
_servers_by_stream: "WeakKeyDictionary[Any, SessionServer]" = WeakKeyDictionary()
_wrapped_transports: list[tuple[Any, Any]] = []


def _stream_key(stream: Any) -> Any:
    # mcp>=2 HTTP transports yield slotted wrappers that cannot be weakly referenced; key on their inner stream.
    return getattr(stream, "_inner", stream)


def client_mcp_server(
    transport: MCPTransport, name: Any = None, url: Any = None, command: Any = None, args: Any = None
) -> Optional[MCPServer]:
    """The mcp_servers entry of a client-managed server from its configuration; None if it cannot be identified.

    Remote servers report their URL origin and stdio servers their command and safe args; never headers or env.
    """
    name = name if isinstance(name, str) and name else None
    if transport == "stdio":
        command = command if isinstance(command, str) and command else None
        stdio_args = args if isinstance(args, (list, tuple)) and all(isinstance(arg, str) for arg in args) else ()
        safe_args = safe_stdio_args(command, stdio_args) if command else []
        return mcp_server("client", transport, name=name, command=command, args=safe_args)
    return mcp_server("client", transport, name=name, url=url if isinstance(url, str) else None)


def _transport_server(transport: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> Optional[MCPServer]:
    """Server of a transport call: the stdio command and safe args, or the URL origin. Never headers or env."""
    if transport == "stdio_client":
        params = args[0] if args else kwargs.get("server")
        return client_mcp_server("stdio", command=getattr(params, "command", None), args=getattr(params, "args", None))
    url = args[0] if args else kwargs.get("url")
    return client_mcp_server("sse" if transport == "sse_client" else "streamable_http", url=url)


class _StreamsRecorder:
    """Async generator proxy that records the server of the first streams a transport yields."""

    __slots__ = ("_agen", "_server")

    def __init__(self, agen: Any, server: SessionServer) -> None:
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
            # The generator asynccontextmanager drives; the transport itself runs unchanged.
            context_manager.gen = _StreamsRecorder(
                context_manager.gen, UNIDENTIFIED_SERVER if server is None else server
            )
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


def client_session_server(session: Any) -> Optional[SessionServer]:
    """Server of an MCP ClientSession, from the transport that produced its read stream.

    UNIDENTIFIED_SERVER when a known transport produced the stream but no key identifies its server, and
    None when the session does not come from a known transport.
    """
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
