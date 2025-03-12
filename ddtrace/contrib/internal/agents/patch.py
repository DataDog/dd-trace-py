"""
Patch module for the OpenAI Agents SDK.
"""
import agents

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap, wrap



def patch():
    """
    Patch the instrumented methods
    """
    if getattr(agents, "_datadog_patch", False):
        return

    setattr(agents, "_datadog_patch", True)

    # TODO: Add wrapping for specific methods
    # Example methods to consider wrapping:
    # - Agent.__init__
    # - AgentRunner.run
    # - Any other relevant methods for tracing


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    if not getattr(agents, "_datadog_patch", False):
        return

    setattr(agents, "_datadog_patch", False)

    # TODO: Add unwrapping for all patched methods
    # Example:
    # unwrap(agents.Agent, "__init__")
    # unwrap(agents.AgentRunner, "run") 