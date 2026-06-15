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

    # AIDEV-NOTE: MLOB-7584 — best-effort; the SDK guards its tracing-processor callbacks
    # but NOT this wrap site, so an unguarded raise here would surface in the user's Runner.run.
    try:
        integration = agents._datadog_integration
        integration.tag_agent_manifest(current_span, args, kwargs, agent_index)

        agent = get_argument_value(args, kwargs, agent_index, "agent", True)
        if agent:
            integration.record_agent_side(
                current_span.trace_id,
                tools_chars=count_tools_chars(agent),
                agent_id=getattr(agent, "name", None),
            )
    except Exception:
        log.debug("openai_agents context_delta agent-side capture failed", exc_info=True)

    return result


async def patched_run_single_turn_module(func, instance, args, kwargs):
    return await _patched_run_single_turn_module(func, instance, args, kwargs)


async def patched_run_single_turn_streamed_module(func, instance, args, kwargs):
    return await _patched_run_single_turn_module(func, instance, args, kwargs)


# AIDEV-NOTE: MLOB-7584 — the module-level per-turn function's call shape varies by
# agents version AND by streamed vs non-streamed variant. Verified against shipped
# wheels 0.8.0-0.17.x:
#   non-streamed 0.8.0-0.13.x:  run_single_turn(agent=<Agent>, ...)             # kwarg
#   non-streamed >= 0.14.0:     run_single_turn(bindings=<AgentBindings>, ...)  # kwarg
#   streamed     0.8.0-0.13.x:  run_single_turn_streamed(<RunResultStreaming>, <Agent>, ...)
#   streamed     >= 0.14.0:     run_single_turn_streamed(<RunResultStreaming>, <AgentBindings>, ...)
# The streamed variants pass the agent/bindings POSITIONALLY at index 1 (index 0 is the
# RunResultStreaming, which exposes only ``current_agent`` — not the binding attrs or a
# bare Agent's name/tools/handoffs, so the scan safely skips it). Reading only
# ``args[0]``/``kwargs["bindings"]`` (the pre-fix behavior) missed the agent= kwarg shape
# (0.8.0-0.13.x) AND the streamed positional shape (all versions), silently dropping the
# manifest + agent-side context capture on those paths.
def _extract_agent_from_module_call(args, kwargs):
    """Resolve the Agent from a module-level run_single_turn[_streamed] call across versions."""
    candidates = []
    for key in ("bindings", "agent"):
        value = kwargs.get(key)
        if value is not None:
            candidates.append(value)
    candidates.extend(arg for arg in args if arg is not None)
    for candidate in candidates:
        # AgentBindings shape (>= 0.14.0): the agent lives under one of these.
        bound_agent = (
            getattr(candidate, "execution_agent", None)
            or getattr(candidate, "public_agent", None)
            or getattr(candidate, "agent", None)
        )
        if bound_agent is not None:
            return bound_agent
        # Bare Agent shape (0.8.0-0.13.x). name + tools + handoffs distinguishes an Agent
        # from RunResultStreaming (which has ``current_agent`` but none of these).
        if hasattr(candidate, "name") and hasattr(candidate, "tools") and hasattr(candidate, "handoffs"):
            return candidate
    return None


async def _patched_run_single_turn_module(func, instance, args, kwargs):
    current_span = tracer.current_span()
    result = await func(*args, **kwargs)

    if current_span is None:
        log.debug("No current span available, skipping tag_agent_manifest")
        return result

    # AIDEV-NOTE: MLOB-7584 — best-effort; the SDK guards its tracing-processor callbacks
    # but NOT this wrap site, so an unguarded raise here would surface in the user's Runner.run.
    try:
        agent = _extract_agent_from_module_call(args, kwargs)
        if agent is None:
            return result

        integration = agents._datadog_integration
        integration.tag_agent_manifest_from_agent(current_span, agent)
        integration.record_agent_side(
            current_span.trace_id,
            tools_chars=count_tools_chars(agent),
            agent_id=getattr(agent, "name", None),
        )
    except Exception:
        log.debug("openai_agents context_delta agent-side capture failed", exc_info=True)

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
