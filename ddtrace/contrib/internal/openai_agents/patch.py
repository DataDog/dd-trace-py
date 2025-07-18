from typing import Dict

import agents
from agents.tracing import add_trace_processor

from ddtrace import config
from ddtrace.contrib.internal.openai_agents.processor import LLMObsTraceProcessor
from ddtrace.internal.utils.version import parse_version
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

def _supported_versions() -> Dict[str, str]:
    return {"agents": ">=0.0.2"}

OPENAI_AGENTS_VERSION = parse_version(get_version())

@with_traced_module_async
async def patched_run_single_turn(agents, pin, func, instance, args, kwargs):
    return await _patched_run_single_turn(agents, pin, func, instance, args, kwargs, agent_index=0)

@with_traced_module_async
async def patched_run_single_turn_streamed(agents, pin, func, instance, args, kwargs):
    return await _patched_run_single_turn(agents, pin, func, instance, args, kwargs, agent_index=1)
    
    
async def _patched_run_single_turn(agents, pin, func, instance, args, kwargs, agent_index=0):
    from ddtrace import tracer
    s = tracer.current_span()
    r = await func(*args, **kwargs)

    agent = get_argument_value(args, kwargs, agent_index, "agent", None)
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

    if OPENAI_AGENTS_VERSION >= (0, 0, 19):
        wrap(agents.run.AgentRunner, "_run_single_turn", patched_run_single_turn(agents))
        wrap(agents.run.AgentRunner, "_run_single_turn_streamed", patched_run_single_turn_streamed(agents))
    else:
        wrap(agents.run.Runner, "_run_single_turn", patched_run_single_turn(agents))
        wrap(agents.run.Runner, "_run_single_turn_streamed", patched_run_single_turn_streamed(agents))


def unpatch():
    """
    Remove instrumentation from patched methods
    """
    if not getattr(agents, "_datadog_patch", False):
        return

    agents._datadog_patch = False

    if OPENAI_AGENTS_VERSION >= (0, 0, 19):
        unwrap(agents.run.AgentRunner, "_run_single_turn")
        unwrap(agents.run.AgentRunner, "_run_single_turn_streamed")
    else:
        unwrap(agents.run.Runner, "_run_single_turn")
        unwrap(agents.run.Runner, "_run_single_turn_streamed")
