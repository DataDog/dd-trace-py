import sys
from dataclasses import replace

import claude_agent_sdk

from ddtrace import config
from ddtrace.contrib.internal.claude_agent_sdk._streaming import handle_streamed_response
from ddtrace.contrib.internal.claude_agent_sdk._streaming import wrap_prompt_if_async_iterable
from ddtrace.contrib.internal.claude_agent_sdk.utils import _retrieve_context
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import ClaudeAgentSdkIntegration


log = get_logger(__name__)


config._add("claude_agent_sdk", {})


def get_version() -> str:
    return getattr(claude_agent_sdk, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"claude_agent_sdk": ">=0.0.23"}


def _make_dd_precompact_hook(instance):
    """Return an async PreCompact hook that records compaction events on the active query.

    The active agent span is looked up via instance._dd_query_args, which traced_client_query
    populates for the duration of a ClaudeSDKClient.query() call.
    """
    async def _hook(input_data, tool_use_id, context):
        try:
            query_args = getattr(instance, "_dd_query_args", None)
            if query_args is None:
                return {}

            # Schema matches dd-apm-test-agent claude_hooks._handle_pre_compact:
            # both fields always present, custom_instructions defaults to "".
            event = {
                "trigger": (input_data or {}).get("trigger") or "",
                "custom_instructions": (input_data or {}).get("custom_instructions") or "",
            }

            # Also stamp on the currently-active step span (if any) so it shows
            # up directly on that span as "compaction happened during this step".
            handler = getattr(instance, "_dd_streaming_handler", None)
            step_span = getattr(handler, "current_step_span", None) if handler else None
            if step_span is not None:
                step_bag = step_span._get_ctx_item("_dd_compactions") or []
                step_bag.append(event)
                step_span._set_ctx_item("_dd_compactions", step_bag)

            query_args.setdefault("compactions", []).append(event)
        except Exception:
            log.warning("Error recording claude_agent_sdk PreCompact event", exc_info=True)
        return {}

    return _hook


def traced_client_init(func, instance, args, kwargs):
    """Inject a ddtrace-owned PreCompact hook into ClaudeSDKClient.options.hooks.

    The SDK reads options.hooks during connect() (via _convert_hooks_to_internal_format),
    so adding our hook between __init__ and connect() is the cleanest injection point.
    To avoid mutating the user's ClaudeAgentOptions object, we build a copy of the
    options (and the hooks dict + PreCompact matcher list) and rebind instance.options.
    """
    func(*args, **kwargs)
    try:
        HookMatcher = getattr(claude_agent_sdk, "HookMatcher", None)
        if HookMatcher is None:
            return
        options = getattr(instance, "options", None)
        if options is None:
            return

        new_hooks = dict(getattr(options, "hooks", None) or {})
        new_matchers = list(new_hooks.get("PreCompact") or [])
        new_matchers.append(HookMatcher(matcher=None, hooks=[_make_dd_precompact_hook(instance)]))
        new_hooks["PreCompact"] = new_matchers

        instance.options = replace(options, hooks=new_hooks)
    except Exception:
        log.warning("Error installing claude_agent_sdk PreCompact hook", exc_info=True)


def traced_query_async_generator(func, _instance, args, kwargs):
    """Trace the standalone query() async generator function."""
    integration = claude_agent_sdk._datadog_integration

    wrapped_args, wrapped_kwargs, prompt_wrapper = wrap_prompt_if_async_iterable(args, kwargs)

    span = integration.trace(
        "claude_agent_sdk.query",
        submit_to_llmobs=True,
        span_name="claude_agent_sdk.query",
    )

    if prompt_wrapper:
        span._set_ctx_item("_dd_prompt_wrapper", prompt_wrapper)

    try:
        resp = func(*wrapped_args, **wrapped_kwargs)
        return handle_streamed_response(integration, resp, args, kwargs, span, operation="query")
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
        "compactions": [],
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
    # Keep _dd_query_args alive (don't reset to None here) so the PreCompact
    # hook can still append to the compactions list during streaming, which is
    # when compactions actually fire. The next traced_client_query call will
    # overwrite it with fresh per-query state.
    compactions = query_args_dict.setdefault("compactions", [])

    if before_context is not None:
        query_kwargs["_dd_before_context"] = before_context
    # Pass the list reference (not a snapshot) so finalize sees any events
    # appended by the hook during the stream that follows.
    query_kwargs["_dd_compactions"] = compactions

    if span is None:
        return func(*args, **kwargs)

    try:
        resp = func(*args, **kwargs)
        return handle_streamed_response(
            integration, resp, query_args, query_kwargs, span, operation="request", instance=instance
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
