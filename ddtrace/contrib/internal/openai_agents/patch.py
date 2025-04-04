import agents
from agents.tracing import add_trace_processor

from ddtrace import config
from ddtrace.contrib.internal.openai_agents.processor import LLMObsTraceProcessor
from ddtrace.contrib.internal.openai_agents.processor import disable_processor
from ddtrace.contrib.internal.openai_agents.processor import enable_processor
from ddtrace.llmobs._integrations.openai_agents import OpenAIAgentsIntegration
from ddtrace.trace import Pin


config._add("openai_agents", {})


def get_version() -> str:
    from agents import version

    return getattr(version, "__version__", "")


def patch():
    """
    Patch the instrumented methods
    """
    if getattr(agents, "_datadog_patch", False):
        return

    agents._datadog_patch = True

    Pin().onto(agents)

    enable_processor()

    add_trace_processor(LLMObsTraceProcessor(OpenAIAgentsIntegration(integration_config=config.openai_agents)))


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    if not getattr(agents, "_datadog_patch", False):
        return

    disable_processor()

    agents._datadog_patch = False
