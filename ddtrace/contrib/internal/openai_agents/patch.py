import importlib

import agents
from agents.tracing import add_trace_processor

from ddtrace import config
from ddtrace.contrib.internal.openai_agents.processor import LLMObsTraceProcessor
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
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

    # AIDEV-NOTE: MLOB-7584 — the SDK doesn't guard this wrap site, so an unguarded raise here
    # would surface in the user's Runner.run.
    try:
        integration = agents._datadog_integration
        integration.tag_agent_manifest(current_span, args, kwargs)
    except Exception:
        log.debug("openai_agents tag_agent_manifest failed", exc_info=True)

    return result


def _has_module_level_run_loop() -> bool:
    # agents >= 0.8.0 introduces the agents.run_internal.run_loop module (see wrap-targets note below).
    try:
        from agents.run_internal import run_loop  # noqa: F401

        return True
    except ImportError:
        return False


# AIDEV-NOTE: MLOB-7584 — agents >= 0.8.0 moved the per-turn fn to agents.run_internal.run_loop. Wrap the
# agents.run re-export (run.py binds the name at import, so the definition is too late); streamed lives in run_loop.
# Both variants share one wrapper — the unified scanner resolves the agent for every shape.
_MODULE_RUN_LOOP_WRAP_TARGETS = [
    ("agents.run", "run_single_turn", _patched_run_single_turn),
    ("agents.run_internal.run_loop", "run_single_turn_streamed", _patched_run_single_turn),
]


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
