"""
Patch module for the OpenAI Agents SDK.
"""
import agents
from agents.tracing import add_trace_processor

from ddtrace.contrib.internal.agents.processor import LLMObsTraceProcessor
from ddtrace.contrib.internal.agents.processor import NoOpTraceProcessor


_span_processor = None


def patch():
    """
    Patch the instrumented methods
    """
    if getattr(agents, "_datadog_patch", False):
        return

    setattr(agents, "_datadog_patch", True)
    global _span_processor
    _span_processor = LLMObsTraceProcessor()

    add_trace_processor(_span_processor)


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    if not getattr(agents, "_datadog_patch", False):
        return
    # since there's no public api to remove a trace processor, we set the instance
    # we added to a no-op instance
    global _span_processor
    _span_processor = NoOpTraceProcessor()

    setattr(agents, "_datadog_patch", False)
