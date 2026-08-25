import inspect
import sys
from typing import Any
from typing import Union

from _ddtrace_internal.modules import check_module_path
import google.adk as adk

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations import GoogleAdkIntegration
from ddtrace.llmobs._integrations.google_utils import extract_provider_and_model_name


logger = get_logger(__name__)

config._add("google_adk", {})


def _supported_versions() -> dict[str, str]:
    return {"google.adk": ">=1.0.0"}


def get_version() -> str:
    return getattr(adk, "__version__", "")


def _traced_agent_run_async(wrapped, instance, args, kwargs):
    """Trace the main execution of an agent (async generator)."""
    integration: GoogleAdkIntegration = adk._datadog_integration
    agent = getattr(instance, "agent", None)
    model = getattr(agent, "model", None)
    provider_name, model_name = extract_provider_and_model_name(instance=model, model_name_attr="model")

    # run_live accepts a deprecated `session` object as an alternative to explicit user_id/session_id
    # keyword arguments. Fall back to the session's fields so its metadata is captured for that path.
    session = kwargs.get("session")
    session_id = kwargs.get("session_id") or getattr(session, "id", None)
    user_id = kwargs.get("user_id") or getattr(session, "user_id", None)
    app_name = getattr(instance, "app_name", None) or getattr(session, "app_name", None)

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
        provider=provider_name,
        model=model_name,
        kind="agent",
        submit_to_llmobs=True,
        _dd_agent=agent,
        **kwargs,
    )

    # Propagate the ADK session id to this span (and its child tool/code-execute spans) at creation
    # time, before the wrapped coroutine produces those children.
    integration.set_session_id(span, session_id)

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
            # Pass a copy with the resolved agent/session metadata so the original call kwargs are
            # left untouched.
            tag_kwargs = dict(kwargs)
            tag_kwargs["instance"] = instance.agent
            tag_kwargs["session_id"] = session_id
            tag_kwargs["user_id"] = user_id
            tag_kwargs["app_name"] = app_name
            integration.llmobs_set_tags(span, args=args, kwargs=tag_kwargs, response=response_events, operation="agent")
            span.finish()

    return _generator()


async def _traced_functions_call_tool_async(wrapped, instance, args, kwargs):
    integration: GoogleAdkIntegration = adk._datadog_integration
    agent = extract_agent_from_tool_context(args, kwargs)
    if agent is None:
        logger.warning("Unable to trace google adk tool call, could not extract agent from tool context.")
        return await wrapped(*args, **kwargs)

    provider_name, model_name = extract_provider_and_model_name(
        instance=getattr(agent, "model", {}), model_name_attr="model"
    )
    # The streaming call path invokes this function with keyword arguments only, so the tool cannot
    # be read from a fixed positional index.
    instance = instance or get_argument_value(args, kwargs, 0, "tool")

    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
        provider=provider_name,
        model=model_name,
        kind="tool",
        submit_to_llmobs=True,
    )

    try:
        result = await wrapped(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="tool")
        span.finish()
        raise

    if inspect.isasyncgen(result):
        # google-adk >= 2.7.0 routes streaming tools through this function, which then returns an
        # async generator. Keep the span open so the streamed items are tagged instead of the
        # generator object.
        return _traced_tool_stream(result, span, integration, args, kwargs)

    integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="tool")
    span.finish()
    return result


async def _traced_tool_stream(agen, span, integration, args, kwargs):
    # Live streams are long-lived, so cap what is retained for tagging.
    chunks = []
    dropped = 0
    try:
        async for item in agen:
            if len(chunks) < MAX_STREAMED_TOOL_CHUNKS:
                chunks.append(item)
            else:
                dropped += 1
            yield item
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # A consumer that stops early closes this generator, but the one it wraps would otherwise
        # wait for async generator finalization. google-adk closes the stream itself, so mirror it.
        await agen.aclose()
        if dropped:
            chunks.append("... %d further streamed items omitted" % dropped)
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=chunks, operation="tool")
        span.finish()


async def _traced_functions_call_tool_live(wrapped, instance, args, kwargs):
    agent = extract_agent_from_tool_context(args, kwargs)
    if agent is None:
        logger.warning("Unable to trace google adk live tool call, could not extract agent from tool context.")
        agen = wrapped(*args, **kwargs)
        async for item in agen:
            yield item

        return

    integration: GoogleAdkIntegration = adk._datadog_integration

    provider_name, model_name = extract_provider_and_model_name(
        instance=getattr(agent, "model", {}), model_name_attr="model"
    )

    with integration.trace(
        "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
        provider=provider_name,
        model=model_name,
        kind="tool",
        submit_to_llmobs=True,
    ) as span:
        result = []
        dropped = 0
        agen = None
        try:
            agen = wrapped(*args, **kwargs)
            async for item in agen:
                if len(result) < MAX_STREAMED_TOOL_CHUNKS:
                    result.append(item)
                else:
                    dropped += 1
                yield item
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            if agen is not None:
                await agen.aclose()
            if dropped:
                result.append("... %d further streamed items omitted" % dropped)
            integration.llmobs_set_tags(
                span,
                args=args,
                kwargs=kwargs,
                response=result,
                operation="tool",
            )


def _traced_code_executor_execute_code(wrapped, instance, args, kwargs):
    """Trace the execution of code by the agent (sync)."""
    integration: GoogleAdkIntegration = adk._datadog_integration
    invocation_context = get_argument_value(args, kwargs, 0, "invocation_context")
    agent = getattr(getattr(invocation_context, "agent", None), "model", {})
    provider_name, model_name = extract_provider_and_model_name(instance=agent, model_name_attr="model")

    # Signature: execute_code(self, invocation_context, code_execution_input)
    with integration.trace(
        "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
        provider=provider_name,
        model=model_name,
        kind="tool",
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


MAX_STREAMED_TOOL_CHUNKS = 100

TOOL_DISPATCH_FUNCTIONS = [
    ("__call_tool_async", _traced_functions_call_tool_async),
    ("__call_tool_live", _traced_functions_call_tool_live),  # removed in google-adk 2.7.0
]

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
    integration: GoogleAdkIntegration = GoogleAdkIntegration(integration_config=config.google_adk)
    setattr(adk, "_datadog_integration", integration)

    # Agent entrypoints (async generators)
    wrap("google.adk", "runners.Runner.run_async", _traced_agent_run_async)
    wrap("google.adk", "runners.Runner.run_live", _traced_agent_run_async)

    # Tool execution (central dispatch). google-adk >= 2.7.0 removed `__call_tool_live` and routes
    # live tool execution through `__call_tool_async`, so only wrap what the installed version has.
    for tool_dispatch_fn, traced_fn in TOOL_DISPATCH_FUNCTIONS:
        if check_module_path(adk, f"flows.llm_flows.functions.{tool_dispatch_fn}"):
            wrap("google.adk", f"flows.llm_flows.functions.{tool_dispatch_fn}", traced_fn)

    # Code executors
    for code_executor in CODE_EXECUTOR_CLASSES:
        if check_module_path(adk, f"code_executors.{code_executor}.execute_code"):
            wrap(
                "google.adk",
                f"code_executors.{code_executor}.execute_code",
                _traced_code_executor_execute_code,
            )


def unpatch():
    """Unpatch the `google.adk` library."""
    if not hasattr(adk, "_datadog_patch") or not getattr(adk, "_datadog_patch"):
        return
    setattr(adk, "_datadog_patch", False)

    unwrap(adk.runners.Runner, "run_async")
    unwrap(adk.runners.Runner, "run_live")

    for tool_dispatch_fn, _ in TOOL_DISPATCH_FUNCTIONS:
        if check_module_path(adk, f"flows.llm_flows.functions.{tool_dispatch_fn}"):
            unwrap(adk.flows.llm_flows.functions, tool_dispatch_fn)

    # Code executors
    for code_executor in CODE_EXECUTOR_CLASSES:
        if check_module_path(adk, f"code_executors.{code_executor}.execute_code"):
            unwrap(getattr(adk.code_executors, code_executor), "execute_code")

    delattr(adk, "_datadog_integration")
