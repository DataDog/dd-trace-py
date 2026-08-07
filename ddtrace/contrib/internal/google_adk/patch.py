import sys
from typing import Any
from typing import Union

import google.adk as adk

from ddtrace import config
from ddtrace.contrib.internal.trace_utils import check_module_path
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations import GoogleAdkIntegration
from ddtrace.llmobs._integrations.google_utils import extract_messages_from_adk_events
from ddtrace.llmobs._integrations.google_utils import extract_provider_and_model_name


logger = get_logger(__name__)

config._add("google_adk", {})

# Bounds on what the agent-run wrapper retains for LLMObs tagging. `run_live` streams events for
# the lifetime of a (potentially unbounded) live agent session, so an attacker able to keep a
# session open or drive many/large events could force the traced process to retain everything and
# exhaust memory (APMSP-3136). We therefore (1) only retain data when LLMObs is enabled — the
# retained data is consumed solely by llmobs_set_tags — and (2) retain the compact extracted
# message representation (which drops raw inline_data/blob bytes) rather than the raw event
# objects, bounded by both a message count and a total character budget so that neither many
# small events nor a few very large ones can grow the buffer without limit.
_MAX_BUFFERED_AGENT_MESSAGES = 10000
_MAX_BUFFERED_AGENT_CHARS = 10 * 1024 * 1024  # 10 MiB of extracted text


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
        # Extract each event into its compact message representation as it streams and retain only
        # that, so raw event objects (which may carry large inline_data/blob payloads) aren't held
        # for the lifetime of a live session. Retention is bounded by both a message count and a
        # character budget, and only happens while LLMObs is enabled — re-checked every iteration
        # so that disabling LLMObs mid-stream (e.g. LLMObs.disable() or remote config) immediately
        # releases the buffer instead of retaining until the session ends (APMSP-3136).
        response_messages: list = []
        retained_chars = 0
        capped = False
        try:
            async for event in agen:
                if not integration.llmobs_enabled:
                    if response_messages:
                        # LLMObs was disabled mid-stream: release what we were holding for it.
                        response_messages = []
                        retained_chars = 0
                elif not capped:
                    for message in extract_messages_from_adk_events(event):
                        if (
                            len(response_messages) >= _MAX_BUFFERED_AGENT_MESSAGES
                            or retained_chars >= _MAX_BUFFERED_AGENT_CHARS
                        ):
                            capped = True
                            logger.warning(
                                "google_adk: response buffer for %s reached its retention cap; "
                                "further messages will not be captured for LLMObs.",
                                wrapped.__name__,
                            )
                            break
                        response_messages.append(message)
                        retained_chars += len(str(message))
                yield event
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            # Pass a copy with the resolved agent/session metadata so the original call kwargs are
            # left untouched. `response` holds the already-extracted messages (see above).
            tag_kwargs = dict(kwargs)
            tag_kwargs["instance"] = instance.agent
            tag_kwargs["session_id"] = session_id
            tag_kwargs["user_id"] = user_id
            tag_kwargs["app_name"] = app_name
            integration.llmobs_set_tags(
                span, args=args, kwargs=tag_kwargs, response=response_messages, operation="agent"
            )
            span.finish()

    return _generator()


async def _traced_functions_call_tool_async(wrapped, instance, args, kwargs):
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
    unwrap(adk.flows.llm_flows.functions, "__call_tool_live")

    # Code executors
    for code_executor in CODE_EXECUTOR_CLASSES:
        if check_module_path(adk, f"code_executors.{code_executor}.execute_code"):
            unwrap(getattr(adk.code_executors, code_executor), "execute_code")

    delattr(adk, "_datadog_integration")
