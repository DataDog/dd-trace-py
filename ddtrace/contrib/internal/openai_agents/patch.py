import agents
from agents.tracing import add_trace_processor

from ddtrace import config
from ddtrace.contrib.internal.openai_agents.processor import LLMObsTraceProcessor
from ddtrace.llmobs._integrations.openai_agents import OpenAIAgentsIntegration
from ddtrace.trace import Pin
from ddtrace.contrib.trace_utils import with_traced_module_async
from ddtrace.contrib.trace_utils import wrap
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.internal.utils import get_argument_value
from ddtrace.contrib.internal.openai_agents.utils import create_agent_manifest
from ddtrace.llmobs._constants import AGENT_MANIFEST


config._add("openai_agents", {})


def get_version() -> str:
    from agents import version

    return getattr(version, "__version__", "")


@with_traced_module_async
async def patched_run_single_turn(agents, pin, func, instance, args, kwargs):
    from ddtrace import tracer
    s = tracer.current_span()
    r = await func(*args, **kwargs)

    agent = get_argument_value(args, kwargs, 0, "agent", None)
    agent_manifest = create_agent_manifest(agent) if agent else None

    s._set_ctx_item(AGENT_MANIFEST, agent_manifest)
    return r

def patch():
    """
    Patch the instrumented methods
    """
    if getattr(agents, "_datadog_patch", False):
        return

    agents._datadog_patch = True

    Pin().onto(agents)

    add_trace_processor(LLMObsTraceProcessor(OpenAIAgentsIntegration(integration_config=config.openai_agents)))
    
    wrap(agents.Runner, "_run_single_turn", patched_run_single_turn(agents))


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    if not getattr(agents, "_datadog_patch", False):
        return

    agents._datadog_patch = False

    unwrap(agents.Runner, "_run_single_turn")
