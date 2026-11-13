import asyncio
from contextvars import ContextVar
import importlib
import inspect
import json
from types import ModuleType
from typing import Any
from typing import Awaitable
from typing import Callable
from typing import Optional

import agents
from agents.tracing import add_trace_processor

from ddtrace import config
from ddtrace.contrib.internal.openai_agents.processor import LLMObsTraceProcessor
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal import core
from ddtrace.internal.logger import get_logger
from ddtrace.internal.module import ModuleWatchdog
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.openai_agents import OpenAIAgentsIntegration
from ddtrace.trace import tracer


log = get_logger(__name__)


config._add("openai_agents", {})


def get_version() -> str:
    from agents import version

    return getattr(version, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"agents": ">=0.0.2"}


async def _patched_run_single_turn(func, instance, args, kwargs):
    current_span = tracer.current_span()
    result = await func(*args, **kwargs)

    if current_span is None:
        log.debug("No current span available, skipping tag_agent_manifest")
        return result

    # MLOB-7584 — the SDK doesn't guard this wrap site, so an unguarded raise here
    # would surface in the user's Runner.run.
    try:
        integration = agents._datadog_integration
        integration.tag_agent_manifest(current_span, args, kwargs)
    except Exception:
        log.debug("openai_agents tag_agent_manifest failed", exc_info=True)

    return result


def _has_module_level_run_loop() -> bool:
    try:
        from agents.run_internal import run_loop  # noqa: F401

        return True
    except ImportError:
        return False


# MLOB-7584 — agents >= 0.8.0 moved the per-turn fn to agents.run_internal.run_loop. Wrap the
# agents.run re-export (run.py binds the name at import, so the definition is too late); streamed lives in run_loop.
# Both variants share one wrapper — the unified scanner resolves the agent for every shape.
_MODULE_RUN_LOOP_WRAP_TARGETS = [
    ("agents.run", "run_single_turn", _patched_run_single_turn),
    ("agents.run_internal.run_loop", "run_single_turn_streamed", _patched_run_single_turn),
]


def _current_task() -> Optional["asyncio.Task[Any]"]:
    # None outside an asyncio task (e.g. under trio): markers then match on their liveness only.
    try:
        return asyncio.current_task()
    except RuntimeError:
        return None


async def _dispatch_mcp_event(event: str, args: tuple[Any, ...]) -> None:
    # Listeners may block on network calls, so they run off the event loop; skipped with no listener.
    if core.has_listeners(event):
        await asyncio.to_thread(core.dispatch, event, args, allow_raise=True)


class _AgentMCPCall:
    """The tool call an MCPUtil.invoke_mcp_tool run announced, consumed once by its server.call_tool.

    Newer SDKs run that call_tool in a task of their own, so any task may consume it, but only for the same
    server, tool and arguments: the call already evaluated.
    """

    __slots__ = ("server", "tool_name", "arguments", "consumed")

    def __init__(self, server: Any, tool_name: Optional[str], arguments: Any) -> None:
        self.server = server
        self.tool_name = tool_name
        self.arguments = arguments
        self.consumed = False


class _CallToolInvocation:
    """A running wrapped server.call_tool, which an override calling super() reaches again.

    Tasks created during it inherit the ContextVar and may outlive it, so only the same task skips, and only
    while the invocation runs.
    """

    __slots__ = ("server_id", "task", "running")

    def __init__(self, server: Any) -> None:
        self.server_id = id(server)
        self.task = _current_task()
        self.running = True

    def is_reentry(self, server: Any) -> bool:
        return self.running and self.server_id == id(server) and self.task is _current_task()


_agent_mcp_call: ContextVar[Optional[_AgentMCPCall]] = ContextVar("dd_openai_agents_agent_mcp_call", default=None)
# Stands for arguments that could not be parsed: it matches no server.call_tool.
_UNPARSED_ARGUMENTS = object()

_MCPWrapper = Callable[[Callable[..., Awaitable[Any]], Any, tuple[Any, ...], dict[str, Any]], Awaitable[Any]]


def _adapter_arguments(input_json: Any) -> Any:
    # The adapter sends json.loads(input_json), or {} when it is empty, to server.call_tool.
    try:
        return json.loads(input_json) if input_json else {}
    except Exception:
        return _UNPARSED_ARGUMENTS


async def _patched_invoke_mcp_tool(
    func: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    # Agent-driven MCP tool run: the model-visible tool and its call context are only known here.
    server = get_argument_value(args, kwargs, 0, "server", optional=True)
    tool = get_argument_value(args, kwargs, 1, "tool", optional=True)
    input_json = get_argument_value(args, kwargs, 3, "input_json", optional=True)
    await _dispatch_mcp_event(
        "openai_agents.mcp.invoke_tool.before",
        (
            server,
            tool,
            get_argument_value(args, kwargs, 2, "context", optional=True),
            input_json,
            kwargs.get("tool_display_name"),
        ),
    )
    # Set on the event loop once the call may proceed, so a blocked call cannot excuse a direct one.
    token = _agent_mcp_call.set(_AgentMCPCall(server, getattr(tool, "name", None), _adapter_arguments(input_json)))
    try:
        return await func(*args, **kwargs)
    finally:
        _agent_mcp_call.reset(token)


# The wrapped call_tool running, so an override calling super() dispatches once while a call forwarded to
# another server, or made by a task created during it, is still dispatched.
_call_tool_invocation: ContextVar[Optional[_CallToolInvocation]] = ContextVar(
    "dd_openai_agents_call_tool_invocation", default=None
)


async def _patched_mcp_server_call_tool(
    func: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    # Lowest MCP call boundary of the SDK servers, also reached by direct server.call_tool calls.
    current = _call_tool_invocation.get()
    if current is not None and current.is_reentry(instance):
        return await func(*args, **kwargs)
    invocation = _CallToolInvocation(instance)
    token = _call_tool_invocation.set(invocation)
    try:
        tool_name = get_argument_value(args, kwargs, 0, "tool_name", optional=True)
        arguments = get_argument_value(args, kwargs, 1, "arguments", optional=True)
        agent_call = _agent_mcp_call.get()
        if (
            agent_call is not None
            and not agent_call.consumed
            and agent_call.server is instance
            and agent_call.tool_name == tool_name
            and agent_call.arguments == (arguments if arguments is not None else {})
        ):
            # Already announced by openai_agents.mcp.invoke_tool.before: the event is for direct calls only.
            agent_call.consumed = True
        else:
            await _dispatch_mcp_event("openai_agents.mcp.call_tool.before", (instance, tool_name, arguments))
        return await func(*args, **kwargs)
    finally:
        invocation.running = False
        _call_tool_invocation.reset(token)


# The SDK imports agents.mcp lazily, as it needs the optional mcp package: wrap it once it is imported.
_MCP_MODULES = ("agents.mcp.util", "agents.mcp.server")
# (owner, attribute, original) of each MCP wrap, restored as-is on unpatch: unwrapping would leave the
# invoke_mcp_tool classmethod bound to MCPUtil, so subclasses would run with cls=MCPUtil.
_mcp_wrapped: list[tuple[Any, str, Any]] = []


def _mcp_wrap_targets(module: ModuleType) -> list[tuple[Any, str, _MCPWrapper]]:
    if module.__name__ == "agents.mcp.util":
        mcp_util = getattr(module, "MCPUtil", None)
        return [(mcp_util, "invoke_mcp_tool", _patched_invoke_mcp_tool)] if mcp_util is not None else []
    base = getattr(module, "MCPServer", None)
    if base is None:
        return []
    # Concrete SDK servers may override call_tool without calling super(), so each class that
    # implements it gets wrapped. The abstract MCPServer.call_tool is never reached.
    targets: list[tuple[Any, str, _MCPWrapper]] = []
    for _, cls in inspect.getmembers(module, inspect.isclass):
        call_tool = cls.__dict__.get("call_tool")
        if (
            cls.__module__ == module.__name__
            and issubclass(cls, base)
            and call_tool is not None
            and not getattr(call_tool, "__isabstractmethod__", False)
        ):
            targets.append((cls, "call_tool", _patched_mcp_server_call_tool))
    return targets


def _wrap_mcp_module(module: ModuleType) -> None:
    try:
        for owner, attr_name, wrapper in _mcp_wrap_targets(module):
            if any(wrapped_owner is owner and name == attr_name for wrapped_owner, name, _ in _mcp_wrapped):
                continue
            original = owner.__dict__[attr_name]
            wrap(owner, attr_name, wrapper)
            _mcp_wrapped.append((owner, attr_name, original))
    except Exception:
        log.debug("openai_agents: failed to instrument %s", module.__name__, exc_info=True)


def patch():
    """
    Patch the instrumented methods
    """
    if getattr(agents, "_datadog_patch", False):
        return

    agents._datadog_patch = True

    integration = OpenAIAgentsIntegration(integration_config=config.openai_agents)
    add_trace_processor(LLMObsTraceProcessor(integration))
    agents._datadog_integration = integration

    if _has_module_level_run_loop():
        for module_path, attr_name, wrapper in _MODULE_RUN_LOOP_WRAP_TARGETS:
            mod = importlib.import_module(module_path)
            if hasattr(mod, attr_name):
                wrap(mod, attr_name, wrapper)
    else:
        runner_cls = getattr(agents.run, "AgentRunner", None) or getattr(agents.run, "Runner", None)
        if runner_cls is not None:
            if hasattr(runner_cls, "_run_single_turn"):
                wrap(runner_cls, "_run_single_turn", _patched_run_single_turn)
            if hasattr(runner_cls, "_run_single_turn_streamed"):
                wrap(runner_cls, "_run_single_turn_streamed", _patched_run_single_turn)

    for module_name in _MCP_MODULES:
        ModuleWatchdog.register_module_hook(module_name, _wrap_mcp_module)


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    if not getattr(agents, "_datadog_patch", False):
        return

    agents._datadog_patch = False

    if _has_module_level_run_loop():
        for module_path, attr_name, _ in _MODULE_RUN_LOOP_WRAP_TARGETS:
            mod = importlib.import_module(module_path)
            if hasattr(mod, attr_name):
                unwrap(mod, attr_name)
    else:
        runner_cls = getattr(agents.run, "AgentRunner", None) or getattr(agents.run, "Runner", None)
        if runner_cls is not None:
            if hasattr(runner_cls, "_run_single_turn"):
                unwrap(runner_cls, "_run_single_turn")
            if hasattr(runner_cls, "_run_single_turn_streamed"):
                unwrap(runner_cls, "_run_single_turn_streamed")

    for module_name in _MCP_MODULES:
        ModuleWatchdog.unregister_module_hook(module_name, _wrap_mcp_module)
    while _mcp_wrapped:
        owner, attr_name, original = _mcp_wrapped.pop()
        setattr(owner, attr_name, original)
