import asyncio
from contextvars import ContextVar
import importlib
import inspect
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
    # A turn runs one model call and the tools it selects, whatever the model provider.
    core.dispatch("openai_agents.turn.started", ())
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


async def _dispatch_mcp_event(event: str, args: tuple[Any, ...]) -> None:
    # Listeners may block on network calls, so they run off the event loop; skipped with no listener.
    if core.has_listeners(event):
        await asyncio.to_thread(core.dispatch, event, args, allow_raise=True)


class _AgentMCPCall:
    """The tool call an MCPUtil.invoke_mcp_tool run announced, consumed by its server.call_tool."""

    __slots__ = ("server", "tool_name", "consumed")

    def __init__(self, server: Any, tool_name: Optional[str]) -> None:
        self.server = server
        self.tool_name = tool_name
        self.consumed = False


_agent_mcp_call: ContextVar[Optional[_AgentMCPCall]] = ContextVar("dd_openai_agents_agent_mcp_call", default=None)

_MCPWrapper = Callable[[Callable[..., Awaitable[Any]], Any, tuple[Any, ...], dict[str, Any]], Awaitable[Any]]


async def _patched_invoke_mcp_tool(
    func: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    # Agent-driven MCP tool run: the model-visible tool and its call context are only known here.
    server = get_argument_value(args, kwargs, 0, "server", optional=True)
    tool = get_argument_value(args, kwargs, 1, "tool", optional=True)
    await _dispatch_mcp_event(
        "openai_agents.mcp.invoke_tool.before",
        (
            server,
            tool,
            get_argument_value(args, kwargs, 2, "context", optional=True),
            get_argument_value(args, kwargs, 3, "input_json", optional=True),
            kwargs.get("tool_display_name"),
        ),
    )
    # Set on the event loop once the call may proceed, so a blocked call cannot excuse a direct one.
    token = _agent_mcp_call.set(_AgentMCPCall(server, getattr(tool, "name", None)))
    try:
        return await func(*args, **kwargs)
    finally:
        _agent_mcp_call.reset(token)


# Set while a wrapped server call_tool runs, so an override calling super() dispatches once.
_in_mcp_server_call_tool: ContextVar[bool] = ContextVar("dd_openai_agents_in_mcp_call_tool", default=False)


async def _patched_mcp_server_call_tool(
    func: Callable[..., Awaitable[Any]], instance: Any, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> Any:
    # Lowest MCP call boundary of the SDK servers, also reached by direct server.call_tool calls.
    if _in_mcp_server_call_tool.get():
        return await func(*args, **kwargs)
    token = _in_mcp_server_call_tool.set(True)
    try:
        tool_name = get_argument_value(args, kwargs, 0, "tool_name", optional=True)
        agent_call = _agent_mcp_call.get()
        if (
            agent_call is not None
            and not agent_call.consumed
            and agent_call.server is instance
            and agent_call.tool_name == tool_name
        ):
            # Already announced by openai_agents.mcp.invoke_tool.before: the event is for direct calls only.
            agent_call.consumed = True
        else:
            await _dispatch_mcp_event(
                "openai_agents.mcp.call_tool.before",
                (instance, tool_name, get_argument_value(args, kwargs, 1, "arguments", optional=True)),
            )
        return await func(*args, **kwargs)
    finally:
        _in_mcp_server_call_tool.reset(token)


def _mcp_wrap_targets() -> list[tuple[Any, str, _MCPWrapper]]:
    """(owner, attribute, wrapper) for the MCP adapter, empty when agents.mcp is unavailable.

    agents.mcp needs the optional mcp package, which is not installable on every Python version.
    """
    try:
        from agents.mcp import server as mcp_server
        from agents.mcp.util import MCPUtil
    except ImportError:
        return []
    targets: list[tuple[Any, str, _MCPWrapper]] = [(MCPUtil, "invoke_mcp_tool", _patched_invoke_mcp_tool)]
    # Concrete SDK servers may override call_tool without calling super(), so each class that
    # implements it gets wrapped. The abstract MCPServer.call_tool is never reached.
    for _, cls in inspect.getmembers(mcp_server, inspect.isclass):
        call_tool = cls.__dict__.get("call_tool")
        if (
            cls.__module__ == mcp_server.__name__
            and issubclass(cls, mcp_server.MCPServer)
            and call_tool is not None
            and not getattr(call_tool, "__isabstractmethod__", False)
        ):
            targets.append((cls, "call_tool", _patched_mcp_server_call_tool))
    return targets


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

    for owner, attr_name, wrapper in _mcp_wrap_targets():
        wrap(owner, attr_name, wrapper)


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

    for owner, attr_name, _ in _mcp_wrap_targets():
        unwrap(owner, attr_name)
