import agents
from agents.tracing import add_trace_processor

from ddtrace import config
from ddtrace.contrib.internal.openai_agents.processor import LLMObsTraceProcessor
from ddtrace.contrib.internal.openai_agents.processor import NoOpTraceProcessor
from ddtrace.llmobs._integrations.openai_agents import OpenAIAgentsIntegration
from ddtrace.trace import Pin


config._add("openai_agents", {})

_span_processor = None


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

    global _span_processor

    Pin().onto(agents)

    _span_processor = LLMObsTraceProcessor(
        integration=OpenAIAgentsIntegration(integration_config=config.openai_agents),
    )
    add_trace_processor(_span_processor)


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    if not getattr(agents, "_datadog_patch", False):
        return

    # Since there's no public API to remove a trace processor, we set the instance
    # we added to a no-op instance
    global _span_processor
    if _span_processor is not None:
        _span_processor._integration.clear_state()
        _span_processor = NoOpTraceProcessor()

    agents._datadog_patch = False
