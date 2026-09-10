import inspect
import sys
from typing import Any
from typing import Union
import weakref

from _ddtrace_internal.modules import check_module_path
import google.adk as adk

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.internal.utils.time import Time
from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations import GoogleAdkIntegration
from ddtrace.llmobs._integrations.google_utils import extract_provider_and_model_name


logger = get_logger(__name__)

config._add("google_adk", {})


def _supported_versions() -> dict[str, str]:
    return {"google.adk": ">=1.0.0"}


def get_version() -> str:
    return getattr(adk, "__version__", "")


GOOGLE_ADK_VERSION = parse_version(get_version())


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

    result = None
    stream_handed_off = False
    exc_info = None

    try:
        result = await wrapped(*args, **kwargs)
        if inspect.isasyncgen(result):
            # google-adk >= 2.7.0 returns an async generator for streaming tools. Keep the span
            # open so the streamed items are tagged instead of the generator object.
            state = {"started": False, "handoff": Time.time_ns() / 1e9}
            stream = _traced_tool_stream(result, span, integration, args, kwargs, state)
            weakref.finalize(
                stream, _finish_unstarted_stream_span, span, integration, args, kwargs, state
            ).atexit = False
            stream_handed_off = True
            return stream
        return result
    except Exception:
        exc_info = sys.exc_info()
        raise
    finally:
        # streamed spans are finished separately by _traced_tool_stream; cancellation finishes here
        if not stream_handed_off:
            if exc_info is not None:
                span.set_exc_info(*exc_info)
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="tool")
            span.finish()


def _finish_unstarted_stream_span(span, integration, args, kwargs, state):
    """Finish a tool span whose stream was handed off but never iterated.

    A generator that never starts never runs its finally, and the non-live dispatch does not
    iterate an async generator result. Runs before the stream's own cleanup, so it defers to
    `started`, and finishes at the handoff rather than at collection time.
    """
    if state["started"] or span.duration_ns is not None:
        return
    try:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="tool")
        span.finish(finish_time=state["handoff"])
    except Exception:
        logger.debug("Error finishing abandoned google adk tool stream span.", exc_info=True)


async def _traced_tool_stream(agen, span, integration, args, kwargs, state=None):
    if state is not None:
        state["started"] = True
    chunks = []
    try:
        async for item in agen:
            chunks.append(item)
            yield item
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # A consumer that stops early closes this generator, but the one it wraps would otherwise
        # wait for async generator finalization. google-adk closes the stream itself, so mirror it.
        # A failure to close must not mask the streamed exception or strand the span.
        try:
            try:
                await agen.aclose()
            except Exception:
                logger.debug("Error closing google adk tool stream.", exc_info=True)
        finally:
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

    # Build the stream before starting the span: an activated span that never finishes would
    # reparent everything after it.
    agen = wrapped(*args, **kwargs)

    # _traced_tool_stream owns the span from here: it tags the streamed items and finishes.
    span = integration.trace(
        "%s.%s" % (instance.__class__.__name__, wrapped.__name__),
        provider=provider_name,
        model=model_name,
        kind="tool",
        submit_to_llmobs=True,
    )

    stream = _traced_tool_stream(agen, span, integration, args, kwargs)
    try:
        async for item in stream:
            yield item
    finally:
        # a consumer that abandons this generator does not close the one it delegates to
        await stream.aclose()


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

    # Tool execution (central dispatch)
    wrap("google.adk", "flows.llm_flows.functions.__call_tool_async", _traced_functions_call_tool_async)
    if GOOGLE_ADK_VERSION < (2, 7, 0):
        wrap("google.adk", "flows.llm_flows.functions.__call_tool_live", _traced_functions_call_tool_live)

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

    unwrap(adk.flows.llm_flows.functions, "__call_tool_async")
    if GOOGLE_ADK_VERSION < (2, 7, 0):
        unwrap(adk.flows.llm_flows.functions, "__call_tool_live")

    # Code executors
    for code_executor in CODE_EXECUTOR_CLASSES:
        if check_module_path(adk, f"code_executors.{code_executor}.execute_code"):
            unwrap(getattr(adk.code_executors, code_executor), "execute_code")

    delattr(adk, "_datadog_integration")
