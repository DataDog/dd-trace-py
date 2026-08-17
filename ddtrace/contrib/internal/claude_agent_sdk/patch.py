import sys

import claude_agent_sdk

from ddtrace import config
from ddtrace.contrib.internal.claude_agent_sdk._streaming import filter_forced_partial_noise
from ddtrace.contrib.internal.claude_agent_sdk._streaming import handle_streamed_response
from ddtrace.contrib.internal.claude_agent_sdk._streaming import wrap_prompt_if_async_iterable
from ddtrace.contrib.internal.claude_agent_sdk.utils import _retrieve_context
from ddtrace.contrib.internal.claude_agent_sdk.utils import force_include_partial_messages
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import ClaudeAgentSdkIntegration
from ddtrace.llmobs._utils import _get_attr


log = get_logger(__name__)


config._add("claude_agent_sdk", {})


def get_version() -> str:
    return getattr(claude_agent_sdk, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"claude_agent_sdk": ">=0.0.23"}


def traced_client_init(func, instance, args, kwargs):
    """Force partial streaming on the client's options before it connects.

    ClaudeSDKClient reads options at construction/connect time (before query()), so
    the flag must be set here rather than on the query() call. We flip the flag in
    place on the client's own options object rather than swapping in a copy, so we
    don't drop later caller mutations. We stash
    whether we forced the flag so receive_messages() can tell the handler to filter the
    extra events.

    A caller-supplied custom transport is constructed independently of these options, so
    forcing (and therefore filtering) the flag there would only risk swallowing chunks the
    transport emits on its own — skip it, exactly as the standalone query() path does.
    """
    func(*args, **kwargs)
    transport = args[1] if len(args) > 1 else kwargs.get("transport")
    if transport is not None:
        instance._dd_forced_partial = False
        return
    try:
        _, forced_partial = force_include_partial_messages(_get_attr(instance, "options", None), in_place=True)
        instance._dd_forced_partial = forced_partial
    except Exception:
        instance._dd_forced_partial = False


def traced_query_async_generator(func, _instance, args, kwargs):
    """Trace the standalone query() async generator function."""
    integration = claude_agent_sdk._datadog_integration

    wrapped_args, wrapped_kwargs, prompt_wrapper = wrap_prompt_if_async_iterable(args, kwargs)

    # Turn on partial streaming so the handler can read accurate per-turn output tokens
    # from the message_delta events. A caller-supplied custom transport is constructed
    # independently of these options, so forcing (and therefore filtering) the flag would
    # only risk swallowing chunks that transport emits on its own — skip it there.
    forced_partial = False
    if wrapped_kwargs.get("transport") is None:
        options, forced_partial = force_include_partial_messages(wrapped_kwargs.get("options"))
        if forced_partial:
            wrapped_kwargs = dict(wrapped_kwargs)
            wrapped_kwargs["options"] = options

    span = integration.trace(
        "claude_agent_sdk.query",
        submit_to_llmobs=True,
        span_name="claude_agent_sdk.query",
    )

    if prompt_wrapper:
        span._set_ctx_item("_dd_prompt_wrapper", prompt_wrapper)

    try:
        resp = func(*wrapped_args, **wrapped_kwargs)
        return handle_streamed_response(
            integration, resp, args, kwargs, span, operation="query", filter_partial=forced_partial
        )
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="query")
        span.finish()
        raise


async def traced_client_query(func, instance, args, kwargs):
    """Trace ClaudeSDKClient.query() - starts span, finished by receive_messages()."""
    # skip tracing for internal /context queries to avoid trace loop
    if getattr(instance, "_dd_internal_context_query", False):
        return await func(*args, **kwargs)

    integration = claude_agent_sdk._datadog_integration

    wrapped_args, wrapped_kwargs, prompt_wrapper = wrap_prompt_if_async_iterable(args, kwargs)

    span = integration.trace(
        "claude_agent_sdk.ClaudeSDKClient.query",
        submit_to_llmobs=True,
        span_name="claude_agent_sdk.ClaudeSDKClient.query",
        instance=instance,
    )

    if prompt_wrapper:
        span._set_ctx_item("_dd_prompt_wrapper", prompt_wrapper)

    before_context = await _retrieve_context(instance)

    instance._dd_query_args = {
        "span": span,
        "args": args,
        "kwargs": kwargs,
        "before_context": before_context,
    }

    try:
        return await func(*wrapped_args, **wrapped_kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="request")
        span.finish()
        instance._dd_query_args = None
        raise


def traced_receive_messages(func, instance, args, kwargs):
    """Trace ClaudeSDKClient.receive_messages() - finishes span started by query()."""
    # skip tracing for internal /context queries to avoid trace loop
    if getattr(instance, "_dd_internal_context_query", False):
        return func(*args, **kwargs)

    integration = claude_agent_sdk._datadog_integration
    query_args_dict = getattr(instance, "_dd_query_args", None) or {}
    span = query_args_dict.get("span")
    query_args = query_args_dict.get("args")
    query_kwargs = query_args_dict.get("kwargs") or {}
    before_context = query_args_dict.get("before_context")
    instance._dd_query_args = None

    if before_context is not None:
        query_kwargs["_dd_before_context"] = before_context

    if span is None:
        resp = func(*args, **kwargs)
        # connect(prompt=...) followed by receive_response() has no traced query() span, but we
        # may still have forced partial streaming on at init. Strip the events we injected so
        # enabling ddtrace never changes the caller's stream, even on this untraced path.
        if getattr(instance, "_dd_forced_partial", False):
            return filter_forced_partial_noise(resp)
        return resp

    try:
        resp = func(*args, **kwargs)
        return handle_streamed_response(
            integration,
            resp,
            query_args,
            query_kwargs,
            span,
            operation="request",
            instance=instance,
            filter_partial=getattr(instance, "_dd_forced_partial", False),
        )
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=query_args, kwargs=query_kwargs, response=None, operation="request")
        span.finish()
        raise


def patch():
    if getattr(claude_agent_sdk, "_datadog_patch", False):
        return

    claude_agent_sdk._datadog_patch = True

    integration = ClaudeAgentSdkIntegration(integration_config=config.claude_agent_sdk)
    claude_agent_sdk._datadog_integration = integration

    wrap("claude_agent_sdk", "query", traced_query_async_generator)
    wrap("claude_agent_sdk", "ClaudeSDKClient.__init__", traced_client_init)
    wrap("claude_agent_sdk", "ClaudeSDKClient.query", traced_client_query)
    wrap("claude_agent_sdk", "ClaudeSDKClient.receive_messages", traced_receive_messages)


def unpatch():
    if not getattr(claude_agent_sdk, "_datadog_patch", False):
        return

    claude_agent_sdk._datadog_patch = False

    unwrap(claude_agent_sdk, "query")
    unwrap(claude_agent_sdk.ClaudeSDKClient, "__init__")
    unwrap(claude_agent_sdk.ClaudeSDKClient, "query")
    unwrap(claude_agent_sdk.ClaudeSDKClient, "receive_messages")

    delattr(claude_agent_sdk, "_datadog_integration")
