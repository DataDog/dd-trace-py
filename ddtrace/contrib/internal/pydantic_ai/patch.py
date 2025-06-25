import functools

from ddtrace import config
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations.base import BaseLLMIntegration
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.llmobs._constants import SPAN_KIND
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils_async import with_traced_module as with_traced_module_async
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.trace import Pin
from ddtrace.contrib.internal.pydantic_ai.utils import tag_request

config._add("pydantic_ai", {})


class PydanticAIIntegration(BaseLLMIntegration):
    _integration_name = "pydantic_ai"


def get_version() -> str:
    import pydantic_ai

    return getattr(pydantic_ai, "__version__", "0.0.0")


@with_traced_module_async
async def traced_agent_run(pydantic_ai, pin, func, instance, args, kwargs):
    integration = pydantic_ai._datadog_integration
    with integration.trace(pin, "Pydantic Agent", submit_to_llmobs=False) as span:
        span.name = instance.name
        tag_request(span, instance)
        return await func(*args, **kwargs)


@with_traced_module_async
async def traced_tool_run(pydantic_ai, pin, func, instance, args, kwargs):
    integration = pydantic_ai._datadog_integration
    with integration.trace(pin, "Pydantic Tool", submit_to_llmobs=False) as span:
        span.name = instance.name
        return await func(*args, **kwargs)


def patch():
    import pydantic_ai

    pydantic_ai._datadog_integration = PydanticAIIntegration(integration_config=config.pydantic_ai)
    Pin().onto(pydantic_ai)

    wrap(pydantic_ai, "agent.Agent.run", traced_agent_run(pydantic_ai))
    wrap(pydantic_ai, "tools.Tool.run", traced_tool_run(pydantic_ai))


def unpatch():
    import pydantic_ai

    unwrap(pydantic_ai.agent.Agent, "run")
    unwrap(pydantic_ai.tools.Tool, "run")

    del pydantic_ai._datadog_integration
    Pin().remove_from(pydantic_ai)
