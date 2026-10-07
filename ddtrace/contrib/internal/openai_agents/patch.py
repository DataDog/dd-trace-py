from contextvars import ContextVar
import importlib
import inspect

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


async def _patched_invoke_mcp_tool(func, instance, args, kwargs):
    # Agent-driven MCP tool run: the model-visible tool and its call context are only known here.
    core.dispatch(
        "openai_agents.mcp.invoke_tool.before",
        (
            get_argument_value(args, kwargs, 0, "server", optional=True),
            get_argument_value(args, kwargs, 1, "tool", optional=True),
            get_argument_value(args, kwargs, 2, "context", optional=True),
            get_argument_value(args, kwargs, 3, "input_json", optional=True),
            kwargs.get("tool_display_name"),
        ),
        allow_raise=True,
    )
    return await func(*args, **kwargs)


# Set while a wrapped server call_tool runs, so an override calling super() dispatches once.
_in_mcp_server_call_tool: ContextVar[bool] = ContextVar("dd_openai_agents_in_mcp_call_tool", default=False)


async def _patched_mcp_server_call_tool(func, instance, args, kwargs):
    # Lowest MCP call boundary of the SDK servers, also reached by direct server.call_tool calls.
    if _in_mcp_server_call_tool.get():
        return await func(*args, **kwargs)
    token = _in_mcp_server_call_tool.set(True)
    try:
        core.dispatch(
            "openai_agents.mcp.call_tool.before",
            (
                instance,
                get_argument_value(args, kwargs, 0, "tool_name", optional=True),
                get_argument_value(args, kwargs, 1, "arguments", optional=True),
            ),
            allow_raise=True,
        )
        return await func(*args, **kwargs)
    finally:
        _in_mcp_server_call_tool.reset(token)


def _mcp_wrap_targets() -> list:
    """(owner, attribute, wrapper) for the MCP adapter, empty when agents.mcp is unavailable.

    agents.mcp needs the optional mcp package, which is not installable on every Python version.
    """
    try:
        from agents.mcp import server as mcp_server
        from agents.mcp.util import MCPUtil
    except ImportError:
        return []
    targets: list = [(MCPUtil, "invoke_mcp_tool", _patched_invoke_mcp_tool)]
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
