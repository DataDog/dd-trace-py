import sys
from typing import Dict

from ddtrace import config
from ddtrace.contrib.internal.pydantic_ai.utils import TracedPydanticAsyncContextManager
from ddtrace.contrib.internal.pydantic_ai.utils import TracedPydanticRunStream
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.llmobs._integrations.pydantic_ai import PydanticAIIntegration
from ddtrace.trace import Pin


config._add("pydantic_ai", {})


def get_version() -> str:
    import pydantic_ai

    return getattr(pydantic_ai, "__version__", "0.0.0")


def _supported_versions() -> Dict[str, str]:
    return {"pydantic_ai": "*"}


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
async def traced_tool_run(pydantic_ai, pin, func, instance, args, kwargs):
    integration = pydantic_ai._datadog_integration
    resp = None
    try:
        span = integration.trace(pin, "Pydantic Tool", submit_to_llmobs=True, kind="tool")
        span.name = getattr(instance, "name", None) or "Pydantic Tool"
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
    wrap(pydantic_ai, "tools.Tool.run", traced_tool_run(pydantic_ai))
    wrap(pydantic_ai, "agent.Agent.run_stream", traced_agent_run_stream(pydantic_ai))


def unpatch():
    import pydantic_ai

    if not getattr(pydantic_ai, "_datadog_patch", False):
        return

    pydantic_ai._datadog_patch = False

    unwrap(pydantic_ai.agent.Agent, "iter")
    unwrap(pydantic_ai.tools.Tool, "run")
    unwrap(pydantic_ai.agent.Agent, "run_stream")

    delattr(pydantic_ai, "_datadog_integration")
    Pin().remove_from(pydantic_ai)
