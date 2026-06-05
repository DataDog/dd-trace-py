import agents
from agents.tracing import add_trace_processor

from ddtrace import config
from ddtrace.contrib.internal.openai_agents.processor import LLMObsTraceProcessor
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.openai_agents import OpenAIAgentsIntegration
from ddtrace.llmobs._integrations.openai_agents import count_tools_chars
from ddtrace.trace import tracer


log = get_logger(__name__)


config._add("openai_agents", {})


def get_version() -> str:
    from agents import version

    return getattr(version, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"agents": ">=0.0.2"}


OPENAI_AGENTS_VERSION = parse_version(get_version())


async def patched_run_single_turn(func, instance, args, kwargs):
    return await _patched_run_single_turn(func, instance, args, kwargs, agent_index=0)


async def patched_run_single_turn_streamed(func, instance, args, kwargs):
    return await _patched_run_single_turn(func, instance, args, kwargs, agent_index=1)


async def _patched_run_single_turn(func, instance, args, kwargs, agent_index=0):
    current_span = tracer.current_span()
    result = await func(*args, **kwargs)

    if current_span is None:
        log.debug("No current span available, skipping tag_agent_manifest")
        return result

    integration = agents._datadog_integration
    integration.tag_agent_manifest(current_span, args, kwargs, agent_index)

    agent = get_argument_value(args, kwargs, agent_index, "agent", True)
    if agent:
        integration.record_agent_side(
            current_span.trace_id,
            tools_chars=count_tools_chars(agent),
            agent_id=getattr(agent, "name", None),
        )

    return result


async def patched_run_single_turn_module(func, instance, args, kwargs):
    return await _patched_run_single_turn_module(func, instance, args, kwargs)


async def patched_run_single_turn_streamed_module(func, instance, args, kwargs):
    return await _patched_run_single_turn_module(func, instance, args, kwargs)


async def _patched_run_single_turn_module(func, instance, args, kwargs):
    current_span = tracer.current_span()
    result = await func(*args, **kwargs)

    if current_span is None:
        log.debug("No current span available, skipping tag_agent_manifest")
        return result

    bindings = args[0] if args else kwargs.get("bindings")
    agent = None
    if bindings is not None:
        agent = (
            getattr(bindings, "execution_agent", None)
            or getattr(bindings, "public_agent", None)
            or getattr(bindings, "agent", None)
        )
    if agent is None:
        return result

    integration = agents._datadog_integration
    integration.tag_agent_manifest_from_agent(current_span, agent)
    integration.record_agent_side(
        current_span.trace_id,
        tools_chars=count_tools_chars(agent),
        agent_id=getattr(agent, "name", None),
    )

    return result


def _has_module_level_run_loop() -> bool:
    try:
        from agents.run_internal import run_loop as _rl  # noqa: F401

        return hasattr(_rl, "run_single_turn") or hasattr(_rl, "run_single_turn_streamed")
    except ImportError:
        return False


# AIDEV-NOTE: MLOB-7584 — wrap ``agents.run.run_single_turn`` (the re-export at
# agents/run.py:84-95), not the definition in ``agents.run_internal.run_loop``. run.py
# imports the symbol at module load, so wrapping the definition module after-the-fact
# has no effect on the call sites. The streamed variant is called from within run_loop
# itself and is wrapped there directly.
_MODULE_RUN_LOOP_WRAP_TARGETS: list[tuple[str, str, str]] = [
    ("agents.run", "run_single_turn", "patched_run_single_turn_module"),
    ("agents.run_internal.run_loop", "run_single_turn_streamed", "patched_run_single_turn_streamed_module"),
]


def _resolve_module(path: str):
    import importlib

    return importlib.import_module(path)


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
        _wrappers_by_name = {
            "patched_run_single_turn_module": patched_run_single_turn_module,
            "patched_run_single_turn_streamed_module": patched_run_single_turn_streamed_module,
        }
        for module_path, attr_name, wrapper_name in _MODULE_RUN_LOOP_WRAP_TARGETS:
            try:
                mod = _resolve_module(module_path)
            except ImportError:
                continue
            if hasattr(mod, attr_name):
                wrap(mod, attr_name, _wrappers_by_name[wrapper_name])
    elif OPENAI_AGENTS_VERSION >= (0, 0, 19):
        if hasattr(agents.run.AgentRunner, "_run_single_turn"):
            wrap(agents.run.AgentRunner, "_run_single_turn", patched_run_single_turn)
        if hasattr(agents.run.AgentRunner, "_run_single_turn_streamed"):
            wrap(agents.run.AgentRunner, "_run_single_turn_streamed", patched_run_single_turn_streamed)
    else:
        if hasattr(agents.run.Runner, "_run_single_turn"):
            wrap(agents.run.Runner, "_run_single_turn", patched_run_single_turn)
        if hasattr(agents.run.Runner, "_run_single_turn_streamed"):
            wrap(agents.run.Runner, "_run_single_turn_streamed", patched_run_single_turn_streamed)


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    if not getattr(agents, "_datadog_patch", False):
        return

    agents._datadog_patch = False

    if _has_module_level_run_loop():
        for module_path, attr_name, _ in _MODULE_RUN_LOOP_WRAP_TARGETS:
            try:
                mod = _resolve_module(module_path)
            except ImportError:
                continue
            if hasattr(mod, attr_name):
                unwrap(mod, attr_name)
    elif OPENAI_AGENTS_VERSION >= (0, 0, 19):
        if hasattr(agents.run.AgentRunner, "_run_single_turn"):
            unwrap(agents.run.AgentRunner, "_run_single_turn")
        if hasattr(agents.run.AgentRunner, "_run_single_turn_streamed"):
            unwrap(agents.run.AgentRunner, "_run_single_turn_streamed")
    else:
        if hasattr(agents.run.Runner, "_run_single_turn"):
            unwrap(agents.run.Runner, "_run_single_turn")
        if hasattr(agents.run.Runner, "_run_single_turn_streamed"):
            unwrap(agents.run.Runner, "_run_single_turn_streamed")
