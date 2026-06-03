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

    # MLOB-7584: capture agent-side context categories (tools + handoffs) for the
    # context_delta payload. First call per trace locks "first"; subsequent calls
    # overwrite "last". Emission happens at on_trace_end in the processor.
    # AIDEV-NOTE: MLOB-7584 — handoffs are folded into the ``tools`` category because the
    # context_delta payload has no dedicated handoffs key. If one is added later, split
    # count_tools_chars back into two separate counts.
    agent = get_argument_value(args, kwargs, agent_index, "agent", True)
    if agent:
        integration.record_agent_side(current_span.trace_id, tools_chars=count_tools_chars(agent))

    return result


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

    if OPENAI_AGENTS_VERSION >= (0, 0, 19):
        if hasattr(agents.run.AgentRunner, "_run_single_turn"):
            wrap(agents.run.AgentRunner, "_run_single_turn", patched_run_single_turn)
        if hasattr(agents.run.AgentRunner, "_run_single_turn_streamed"):
            wrap(agents.run.AgentRunner, "_run_single_turn_streamed", patched_run_single_turn_streamed)
    else:
        wrap(agents.run.Runner, "_run_single_turn", patched_run_single_turn)
        wrap(agents.run.Runner, "_run_single_turn_streamed", patched_run_single_turn_streamed)


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    if not getattr(agents, "_datadog_patch", False):
        return

    agents._datadog_patch = False

    if OPENAI_AGENTS_VERSION >= (0, 0, 19):
        if hasattr(agents.run.AgentRunner, "_run_single_turn"):
            unwrap(agents.run.AgentRunner, "_run_single_turn")
        if hasattr(agents.run.AgentRunner, "_run_single_turn_streamed"):
            unwrap(agents.run.AgentRunner, "_run_single_turn_streamed")
    else:
        unwrap(agents.run.Runner, "_run_single_turn")
        unwrap(agents.run.Runner, "_run_single_turn_streamed")
