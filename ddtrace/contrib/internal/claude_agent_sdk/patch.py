import sys

import claude_agent_sdk

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.contrib.internal.claude_agent_sdk._streaming import handle_streamed_response
from ddtrace.contrib.internal.claude_agent_sdk.utils import _retrieve_context
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import ClaudeAgentSdkIntegration


log = get_logger(__name__)


config._add("claude_agent_sdk", {})


def get_version() -> str:
    return getattr(claude_agent_sdk, "__version__", "")


def _supported_versions() -> dict[str, str]:
    return {"claude_agent_sdk": ">=0.0.23"}


@with_traced_module
def traced_query_async_generator(claude_agent_sdk, pin, func, _instance, args, kwargs):
    """Trace the standalone query() async generator function."""
    integration = claude_agent_sdk._datadog_integration

    span = integration.trace(
        pin,
        "claude_agent_sdk.query",
        submit_to_llmobs=True,
    )

    try:
        resp = func(*args, **kwargs)
        return handle_streamed_response(integration, resp, args, kwargs, span, operation="query", pin=pin)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="query")
        span.finish()
        raise


@with_traced_module
async def traced_client_query(claude_agent_sdk, pin, func, instance, args, kwargs):
    """Trace ClaudeSDKClient.query() - starts span, finished by receive_messages()."""
    # skip tracing for internal /context queries to avoid trace loop
    if getattr(instance, "_dd_internal_context_query", False):
        return await func(*args, **kwargs)

    integration = claude_agent_sdk._datadog_integration

    span = integration.trace(
        pin,
        "claude_agent_sdk.ClaudeSDKClient.query",
        submit_to_llmobs=True,
        instance=instance,
    )

    before_context = await _retrieve_context(instance)

    instance._dd_query_args = {"span": span, "args": args, "kwargs": kwargs, "before_context": before_context}

    try:
        return await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="request")
        span.finish()
        instance._dd_query_args = None
        raise


@with_traced_module
def traced_receive_messages(claude_agent_sdk, pin, func, instance, args, kwargs):
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
        return func(*args, **kwargs)

    try:
        resp = func(*args, **kwargs)
        return handle_streamed_response(
            integration, resp, query_args, query_kwargs, span, operation="request", pin=pin, instance=instance
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

    Pin().onto(claude_agent_sdk)
    integration = ClaudeAgentSdkIntegration(integration_config=config.claude_agent_sdk)
    claude_agent_sdk._datadog_integration = integration

    wrap("claude_agent_sdk", "query", traced_query_async_generator(claude_agent_sdk))
    wrap("claude_agent_sdk", "ClaudeSDKClient.query", traced_client_query(claude_agent_sdk))
    wrap("claude_agent_sdk", "ClaudeSDKClient.receive_messages", traced_receive_messages(claude_agent_sdk))


def unpatch():
    if not getattr(claude_agent_sdk, "_datadog_patch", False):
        return

    claude_agent_sdk._datadog_patch = False

    unwrap(claude_agent_sdk, "query")
    unwrap(claude_agent_sdk.ClaudeSDKClient, "query")
    unwrap(claude_agent_sdk.ClaudeSDKClient, "receive_messages")

    delattr(claude_agent_sdk, "_datadog_integration")
