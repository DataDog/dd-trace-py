"""Instrument google-adk."""
from typing import Any
from typing import Dict
from typing import Optional
from typing import Tuple

import google.adk as adk

from ddtrace import config
from ddtrace._trace._limits import TRUNCATED_SPAN_ATTRIBUTE_LEN
from ddtrace._trace.pin import Pin
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import GoogleAdkIntegration


logger = get_logger(__name__)

config._add("google_adk", {})


def _supported_versions() -> Dict[str, str]:
    return {"google.adk": ">=1.0.0"}


def get_version() -> str:
    return getattr(adk, "__version__", "")


def _truncate_value(value):
    try:
        s = str(value)
    except Exception:
        s = repr(value)
    if len(s) > TRUNCATED_SPAN_ATTRIBUTE_LEN:
        return s[:TRUNCATED_SPAN_ATTRIBUTE_LEN]
    return s


@with_traced_module
def _traced_agent_run_async(adk, pin, wrapped, instance, args, kwargs):
    """Trace the main execution of an agent (async generator)."""
    integration = adk._datadog_integration
    agen = wrapped(*args, **kwargs)

    async def _generator():
        provider_name, model_name = extract_provider_and_model_name(agent=instance.agent)
        with integration.trace(
            pin,
            "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
            provider=provider_name,
            model=model_name,
            kind="agent",
            submit_to_llmobs=True,
            run_kwargs=kwargs,
        ) as span:
            response_events = []
            try:
                async for event in agen:
                    response_events.append(event)
                    yield event
            except Exception as e:
                span.set_exc_info(type(e), e, e.__traceback__)
                raise
            finally:
                span.set_tag_str("component", "google-adk")
                kwargs["instance"] = instance.agent
                integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response_events)

    return _generator()


@with_traced_module
async def _traced_functions_call_tool_async(adk, pin, wrapped, instance, args, kwargs):
    integration = adk._datadog_integration
    tool_context = kwargs.get("tool_context", {})
    # Handle cases where invocation context might not exist (e.g., direct tool calls)
    agent = None
    if hasattr(tool_context, "_invocation_context") and hasattr(tool_context._invocation_context, "agent"):
        agent = tool_context._invocation_context.agent
    provider_name, model_name = extract_provider_and_model_name(agent=agent)
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
        except Exception as e:
            span.set_exc_info(type(e), e, e.__traceback__)
            raise
        finally:
            span.set_tag_str("component", "google-adk")
            kwargs["tool"] = args[0] if args else kwargs.get("tool")
            kwargs["tool_args"] = args[1] if len(args) > 1 else kwargs.get("args")
            kwargs["tool_call_id"] = tool_context.function_call_id
            integration.llmobs_set_tags(
                span,
                args=args,
                kwargs=kwargs,
                response=result,
            )


@with_traced_module
async def _traced_functions_call_tool_live(adk, pin, wrapped, instance, args, kwargs):
    integration = adk._datadog_integration
    tool_context = kwargs.get("tool_context", {})
    agent = tool_context._invocation_context.agent
    provider_name, model_name = extract_provider_and_model_name(agent=agent)

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
        except Exception as e:
            span.set_exc_info(type(e), e, e.__traceback__)
            raise
        finally:
            span.set_tag_str("component", "google-adk")
            kwargs["tool"] = args[0] if args else kwargs.get("tool")
            kwargs["tool_args"] = args[1] if len(args) > 1 else kwargs.get("args")
            kwargs["tool_call_id"] = tool_context.function_call_id
            integration.llmobs_set_tags(
                span,
                args=args,
                kwargs=kwargs,
                response=result,
            )


@with_traced_module
def _traced_code_executor_execute_code(adk, pin, wrapped, instance, args, kwargs):
    """Trace the execution of code by the agent (sync)."""
    integration = adk._datadog_integration
    invocation_context = kwargs.get("invocation_context", args[0] if args else None)
    provider_name, model_name = extract_provider_and_model_name(agent=getattr(invocation_context, "agent", None))

    # Signature: execute_code(self, invocation_context, code_execution_input)
    code_input = kwargs.get("code_execution_input", args[1] if len(args) >= 2 and args[1] else None)
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
        except Exception as e:
            span.set_exc_info(type(e), e, e.__traceback__)
            raise
        finally:
            span.set_tag_str("component", "google-adk")
            kwargs["code_input"] = getattr(code_input, "code", None)
            integration.llmobs_set_tags(
                span,
                args=args,
                kwargs=kwargs,
                response=result,
            )


def extract_provider_and_model_name(kwargs: Optional[Dict[str, Any]] = None, agent: Any = None) -> Tuple[str, str]:
    if agent is None:
        return "google", "google"
    return agent.model.__class__.__name__, agent.model.model


CODE_EXECUTOR_CLASSES = [
    "BuiltInCodeExecutor",  # make an external llm tool call to use the llms built in code executor
    "VertexAiCodeExecutor",
    "UnsafeLocalCodeExecutor",
    "ContainerCodeExecutor",  # additional package dependendy
]

# MEMORY_SERVICE_CLASSES = [ # TODO: Add memory tracing
#     "InMemoryMemoryService",
#     "VertexAiMemoryBankService",
#     "VertexAiRagMemoryService",
# ]


def patch():
    """Patch the `google.adk` library for tracing."""

    if getattr(adk, "_datadog_patch", False):
        return

    setattr(adk, "_datadog_patch", True)
    Pin().onto(adk)
    integration = GoogleAdkIntegration(integration_config=config.google_adk)
    setattr(adk, "_datadog_integration", integration)

    # Agent entrypoints (async generators)
    wrap("google.adk", "runners.Runner.run_async", _traced_agent_run_async(adk))
    wrap("google.adk", "runners.Runner.run_live", _traced_agent_run_async(adk))

    # Tool execution (central dispatch)
    if hasattr(adk, "flows") and hasattr(adk.flows, "llm_flows") and hasattr(adk.flows.llm_flows, "functions"):
        funcs = adk.flows.llm_flows.functions
        if hasattr(funcs, "__call_tool_async"):
            wrap("google.adk", "flows.llm_flows.functions.__call_tool_async", _traced_functions_call_tool_async(adk))
        if hasattr(funcs, "__call_tool_live"):
            wrap("google.adk", "flows.llm_flows.functions.__call_tool_live", _traced_functions_call_tool_live(adk))

    if hasattr(adk, "code_executors"):
        for code_executor in CODE_EXECUTOR_CLASSES:
            try:
                executor_cls = getattr(adk.code_executors, code_executor, None)
                if executor_cls is not None and hasattr(executor_cls, "execute_code"):
                    wrap(
                        "google.adk",
                        f"code_executors.{code_executor}.execute_code",
                        _traced_code_executor_execute_code(adk),
                    )
            except ImportError:
                pass


def unpatch():
    """Unpatch the `google.adk` library."""
    if not hasattr(adk, "_datadog_patch") or not getattr(adk, "_datadog_patch"):
        return
    setattr(adk, "_datadog_patch", False)

    unwrap(adk.runners.Runner, "run_async")
    unwrap(adk.runners.Runner, "run_live")

    if hasattr(adk, "flows") and hasattr(adk.flows, "llm_flows") and hasattr(adk.flows.llm_flows, "functions"):
        funcs = adk.flows.llm_flows.functions
        if hasattr(funcs, "__call_tool_async"):
            unwrap(funcs, "__call_tool_async")
        if hasattr(funcs, "__call_tool_live"):
            unwrap(funcs, "__call_tool_live")

    if hasattr(adk, "code_executors"):
        for code_executor in CODE_EXECUTOR_CLASSES:
            try:
                executor_cls = getattr(adk.code_executors, code_executor, None)
                if executor_cls is not None and hasattr(executor_cls, "execute_code"):
                    unwrap(executor_cls, "execute_code")
            except ImportError:
                # Some code executors require additional dependencies, skip them
                continue

    delattr(adk, "_datadog_integration")
