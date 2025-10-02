import sys
from typing import Any
from typing import Dict
from typing import Union

import google.adk as adk

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.trace_utils import check_module_path
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations import GoogleAdkIntegration
from ddtrace.llmobs._integrations.google_utils import extract_provider_and_model_name


logger = get_logger(__name__)

config._add("google_adk", {})


def _supported_versions() -> Dict[str, str]:
    return {"google.adk": ">=1.0.0"}


def get_version() -> str:
    return getattr(adk, "__version__", "")


@with_traced_module
def _traced_agent_run_async(adk, pin, wrapped, instance, args, kwargs):
    """Trace the main execution of an agent (async generator)."""
    integration: GoogleAdkIntegration = adk._datadog_integration
    agent = getattr(instance, "agent", None)
    model = getattr(agent, "model", None)
    provider_name, model_name = extract_provider_and_model_name(instance=model, model_name_attr="model")

    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
        provider=provider_name,
        model=model_name,
        kind="agent",
        submit_to_llmobs=True,
        **kwargs,
    )

    try:
        agen = wrapped(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise

    async def _generator():
        response_events = []
        try:
            async for event in agen:
                response_events.append(event)
                yield event
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            kwargs["instance"] = instance.agent
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response_events, operation="agent")
            span.finish()
            del kwargs["instance"]

    return _generator()


@with_traced_module
async def _traced_functions_call_tool_async(adk, pin, wrapped, instance, args, kwargs):
    integration: GoogleAdkIntegration = adk._datadog_integration
    agent = extract_agent_from_tool_context(args, kwargs)
    if agent is None:
        logger.warning("Unable to trace google adk live tool call, could not extract agent from tool context.")
        return wrapped(*args, **kwargs)

    provider_name, model_name = extract_provider_and_model_name(
        instance=getattr(agent, "model", {}), model_name_attr="model"
    )
    instance = instance or args[0]

    with integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
        provider=provider_name,
        model=model_name,
        kind="tool",
        submit_to_llmobs=True,
    ) as span:
        result = None
        try:
            result = await wrapped(*args, **kwargs)
            return result
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            integration.llmobs_set_tags(
                span,
                args=args,
                kwargs=kwargs,
                response=result,
                operation="tool",
            )


@with_traced_module
async def _traced_functions_call_tool_live(adk, pin, wrapped, instance, args, kwargs):
    integration: GoogleAdkIntegration = adk._datadog_integration
    agent = extract_agent_from_tool_context(args, kwargs)
    if agent is None:
        logger.warning("Unable to trace google adk live tool call, could not extract agent from tool context.")
        agen = wrapped(*args, **kwargs)
        async for item in agen:
            yield item

    provider_name, model_name = extract_provider_and_model_name(
        instance=getattr(agent, "model", {}), model_name_attr="model"
    )

    with integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
        provider=provider_name,
        model=model_name,
        kind="tool",
        submit_to_llmobs=True,
    ) as span:
        result = None
        try:
            agen = wrapped(*args, **kwargs)
            async for item in agen:
                yield item
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            integration.llmobs_set_tags(
                span,
                args=args,
                kwargs=kwargs,
                response=result,
                operation="tool",
            )


@with_traced_module
def _traced_code_executor_execute_code(adk, pin, wrapped, instance, args, kwargs):
    """Trace the execution of code by the agent (sync)."""
    integration: GoogleAdkIntegration = adk._datadog_integration
    invocation_context = get_argument_value(args, kwargs, 0, "invocation_context")
    agent = getattr(getattr(invocation_context, "agent", None), "model", {})
    provider_name, model_name = extract_provider_and_model_name(instance=agent, model_name_attr="model")

    # Signature: execute_code(self, invocation_context, code_execution_input)
    with integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
        provider=provider_name,
        model=model_name,
        kind="code_execute",
        submit_to_llmobs=True,
    ) as span:
        result = None
        try:
            result = wrapped(*args, **kwargs)
            return result
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            integration.llmobs_set_tags(
                span,
                args=args,
                kwargs=kwargs,
                response=result,
                operation="code_execute",
            )


def extract_agent_from_tool_context(args: Any, kwargs: Any) -> Union[str, None]:
    tool_context = get_argument_value(args, kwargs, 2, "tool_context")
    agent = None
    if hasattr(tool_context, "_invocation_context") and hasattr(tool_context._invocation_context, "agent"):
        agent = tool_context._invocation_context.agent
    return agent


CODE_EXECUTOR_CLASSES = [
    "BuiltInCodeExecutor",  # make an external llm tool call to use the llms built in code executor
    "VertexAiCodeExecutor",
    "UnsafeLocalCodeExecutor",
    "ContainerCodeExecutor",  # additional package dependendy
]


def patch():
    """Patch the `google.adk` library for tracing."""

    if getattr(adk, "_datadog_patch", False):
        return

    setattr(adk, "_datadog_patch", True)
    Pin().onto(adk)
    integration: GoogleAdkIntegration = GoogleAdkIntegration(integration_config=config.google_adk)
    setattr(adk, "_datadog_integration", integration)

    # Agent entrypoints (async generators)
    wrap("google.adk", "runners.Runner.run_async", _traced_agent_run_async(adk))
    wrap("google.adk", "runners.Runner.run_live", _traced_agent_run_async(adk))

    # Tool execution (central dispatch)
    wrap("google.adk", "flows.llm_flows.functions.__call_tool_async", _traced_functions_call_tool_async(adk))
    wrap("google.adk", "flows.llm_flows.functions.__call_tool_live", _traced_functions_call_tool_live(adk))

    # Code executors
    for code_executor in CODE_EXECUTOR_CLASSES:
        if check_module_path(adk, f"code_executors.{code_executor}.execute_code"):
            wrap(
                "google.adk",
                f"code_executors.{code_executor}.execute_code",
                _traced_code_executor_execute_code(adk),
            )


def unpatch():
    """Unpatch the `google.adk` library."""
    if not hasattr(adk, "_datadog_patch") or not getattr(adk, "_datadog_patch"):
        return
    setattr(adk, "_datadog_patch", False)

    unwrap(adk.runners.Runner, "run_async")
    unwrap(adk.runners.Runner, "run_live")

    unwrap(adk.flows.llm_flows.functions, "__call_tool_async")
    unwrap(adk.flows.llm_flows.functions, "__call_tool_live")

    # Code executors
    for code_executor in CODE_EXECUTOR_CLASSES:
        if check_module_path(adk, f"code_executors.{code_executor}.execute_code"):
            unwrap(getattr(adk.code_executors, code_executor), "execute_code")

    delattr(adk, "_datadog_integration")
