import sys
from typing import Dict

from ddtrace import config
from ddtrace.contrib.internal.pydantic_ai.utils import TracedPydanticAsyncContextManager
from ddtrace.contrib.internal.pydantic_ai.utils import TracedPydanticRunStream
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.pydantic_ai import PydanticAIIntegration
from ddtrace.trace import Pin


config._add("pydantic_ai", {})


def get_version() -> str:
    import pydantic_ai

    return getattr(pydantic_ai, "__version__", "0.0.0")


def _supported_versions() -> Dict[str, str]:
    return {"pydantic_ai": "*"}


PYDANTIC_AI_VERSION = parse_version(get_version())


@with_traced_module
def traced_agent_run_stream(pydantic_ai, pin, func, instance, args, kwargs):
    integration = pydantic_ai._datadog_integration
    integration._run_stream_active = True
    span = integration.trace(
        pin, "Pydantic Agent", submit_to_llmobs=True, model=getattr(instance, "model", None), kind="agent"
    )
    span.name = getattr(instance, "name", None) or "Pydantic Agent"

    result = func(*args, **kwargs)
    kwargs["instance"] = instance
    return TracedPydanticRunStream(result, span, integration, args, kwargs)


@with_traced_module
def traced_agent_iter(pydantic_ai, pin, func, instance, args, kwargs):
    integration = pydantic_ai._datadog_integration
    # avoid double tracing if run_stream has already been called
    if integration._run_stream_active:
        integration._run_stream_active = False
        return func(*args, **kwargs)
    span = integration.trace(
        pin, "Pydantic Agent", submit_to_llmobs=True, model=getattr(instance, "model", None), kind="agent"
    )
    span.name = getattr(instance, "name", None) or "Pydantic Agent"

    result = func(*args, **kwargs)
    kwargs["instance"] = instance
    return TracedPydanticAsyncContextManager(result, span, instance, integration, args, kwargs)


@with_traced_module
async def traced_tool_manager_call(pydantic_ai, pin, func, instance, args, kwargs):
    tool_call = get_argument_value(args, kwargs, 0, "tool_call", True)
    tool_name = getattr(tool_call, "tool_name", None) or "Pydantic Tool"
    tool_manager_tools = getattr(instance, "tools", {}) or {}
    tool_instance = tool_manager_tools.get(tool_name) or None
    return await traced_tool_run(pydantic_ai, pin, func, tool_instance, args, kwargs, tool_name)


@with_traced_module
async def traced_tool_call(pydantic_ai, pin, func, instance, args, kwargs):
    tool_name = getattr(instance, "name", None) or "Pydantic Tool"
    return await traced_tool_run(pydantic_ai, pin, func, instance, args, kwargs, tool_name)


async def traced_tool_run(pydantic_ai, pin, func, instance, args, kwargs, tool_name):
    integration = pydantic_ai._datadog_integration
    resp = None
    try:
        span = integration.trace(pin, "Pydantic Tool", submit_to_llmobs=True, kind="tool")
        span.name = tool_name
        resp = await func(*args, **kwargs)
        return resp
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        kwargs["instance"] = instance
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=resp)
        span.finish()


def patch():
    import pydantic_ai

    if getattr(pydantic_ai, "_datadog_patch", False):
        return

    pydantic_ai._datadog_patch = True

    Pin().onto(pydantic_ai)
    pydantic_ai._datadog_integration = PydanticAIIntegration(integration_config=config.pydantic_ai)

    wrap(pydantic_ai, "agent.Agent.iter", traced_agent_iter(pydantic_ai))
    wrap(pydantic_ai, "agent.Agent.run_stream", traced_agent_run_stream(pydantic_ai))
    if PYDANTIC_AI_VERSION >= (0, 4, 4):
        wrap(pydantic_ai, "agent.ToolManager.handle_call", traced_tool_manager_call(pydantic_ai))
    else:
        wrap(pydantic_ai, "tools.Tool.run", traced_tool_call(pydantic_ai))


def unpatch():
    import pydantic_ai

    if not getattr(pydantic_ai, "_datadog_patch", False):
        return

    pydantic_ai._datadog_patch = False

    unwrap(pydantic_ai.agent.Agent, "iter")
    unwrap(pydantic_ai.agent.Agent, "run_stream")
    if PYDANTIC_AI_VERSION >= (0, 4, 4):
        unwrap(pydantic_ai.agent.ToolManager, "handle_call")
    else:
        unwrap(pydantic_ai.tools.Tool, "run")

    delattr(pydantic_ai, "_datadog_integration")
    Pin().remove_from(pydantic_ai)
