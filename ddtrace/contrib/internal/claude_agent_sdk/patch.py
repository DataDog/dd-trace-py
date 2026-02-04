import sys
from typing import Dict

import claude_agent_sdk

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.claude_agent_sdk._streaming import handle_streamed_response
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.ext import SpanKind
from ddtrace.ext import net
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import ClaudeAgentSdkIntegration


log = get_logger(__name__)


config._add("claude_agent_sdk", {})


def get_version() -> str:
    return getattr(claude_agent_sdk, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"claude_agent_sdk": ">=0.1.0"}


@with_traced_module
def traced_query_async_generator(claude_agent_sdk, pin, func, _instance, args, kwargs):
    """Trace the standalone query() async generator function."""
    integration = claude_agent_sdk._datadog_integration

    span = integration.trace(
        pin,
        "claude_agent_sdk.query",
        submit_to_llmobs=True,
        span_name="claude_agent_sdk.query",
    )
    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span._set_tag_str(net.TARGET_HOST, "api.anthropic.com")

    try:
        resp = func(*args, **kwargs)
        return handle_streamed_response(integration, resp, args, kwargs, span, operation="query")
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
        "claude_agent_sdk.request",
        submit_to_llmobs=True,
        span_name="claude_agent_sdk.request",
        instance=instance,
    )

    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span._set_tag_str(net.TARGET_HOST, "api.anthropic.com")

    # set flag to skip tracing during internal context retrieval
    before_context = None
    try:
        instance._dd_internal_context_query = True
        await instance.query("/context")
        context_messages = []
        async for msg in instance.receive_response():
            context_messages.append(msg)
        before_context = context_messages
    except Exception:
        log.warning("Error retrieving before context", exc_info=True)
    finally:
        instance._dd_internal_context_query = False

    instance._datadog_span = span
    instance._datadog_query_args = args
    instance._datadog_query_kwargs = kwargs
    instance._datadog_before_context = before_context

    try:
        return await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="request")
        span.finish()
        instance._datadog_span = None
        instance._datadog_before_context = None
        raise


@with_traced_module
def traced_receive_messages(claude_agent_sdk, pin, func, instance, args, kwargs):
    """Trace ClaudeSDKClient.receive_messages() - finishes span started by query()."""
    if getattr(instance, "_dd_internal_context_query", False):
        return func(*args, **kwargs)

    integration = claude_agent_sdk._datadog_integration
    span = getattr(instance, "_datadog_span", None)
    query_args = getattr(instance, "_datadog_query_args", ())
    query_kwargs = getattr(instance, "_datadog_query_kwargs", {})
    before_context = getattr(instance, "_datadog_before_context", None)

    if before_context is not None:
        query_kwargs["_dd_before_context"] = before_context

    # Clear references now that we're taking ownership
    if span:
        instance._datadog_span = None
    if before_context:
        instance._datadog_before_context = None

    # If no span exists, create one (shouldn't normally happen)
    if span is None:
        span = integration.trace(
            pin,
            "claude_agent_sdk.request",
            submit_to_llmobs=True,
            span_name="claude_agent_sdk.request",
            instance=instance,
        )
        span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        span._set_tag_str(net.TARGET_HOST, "api.anthropic.com")

    try:
        resp = func(*args, **kwargs)
        # Use query_args/query_kwargs for tagging since they contain the actual request parameters
        # Pass instance to enable context retrieval
        return handle_streamed_response(integration, resp, query_args, query_kwargs, span, operation="request", instance=instance)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=query_args, kwargs=query_kwargs, response=None, operation="request")
        span.finish()
        raise


def patch():
    """Activate tracing for claude_agent_sdk."""
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
    """Disable tracing for claude_agent_sdk."""
    if not getattr(claude_agent_sdk, "_datadog_patch", False):
        return

    claude_agent_sdk._datadog_patch = False

    unwrap(claude_agent_sdk, "query")
    unwrap(claude_agent_sdk.ClaudeSDKClient, "query")
    unwrap(claude_agent_sdk.ClaudeSDKClient, "receive_messages")

    delattr(claude_agent_sdk, "_datadog_integration")
