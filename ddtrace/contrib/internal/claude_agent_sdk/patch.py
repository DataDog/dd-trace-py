import sys
from typing import Dict

import claude_agent_sdk

from ddtrace import config
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import wrap
from ddtrace.ext import SpanKind
from ddtrace.ext import net
from ddtrace.llmobs._integrations import ClaudeAgentSdkIntegration

config._add("claude_agent_sdk", {})

def get_version() -> str:
    return getattr(claude_agent_sdk, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"claude_agent_sdk": ">=0.1.0"}


def _create_query_span(integration, operation_name, instance):
    """Helper to create a query span with common tags."""
    span = integration.trace(
        operation_name, submit_to_llmobs=True, span_name=operation_name, model="", instance=instance
    )
    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span._set_tag_str(net.TARGET_HOST, "api.anthropic.com")
    return span


async def traced_query(func, instance, args, kwargs):
    """Trace the standalone query() async generator function."""
    integration = claude_agent_sdk._datadog_integration

    span = _create_query_span(integration, "claude_agent_sdk.query", instance)
    response_messages = []
    try:
        async for message in func(*args, **kwargs):
            response_messages.append(message)
            yield message
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response_messages, operation="query")
        span.finish()


async def traced_client_query(func, instance, args, kwargs):
    """Trace ClaudeSDKClient.query() - starts span, finished by receive_messages()."""
    if not hasattr(instance, "_datadog_queries"):
        instance._datadog_queries = []
    instance._datadog_queries.append((args, kwargs))

    try:
        return await func(*args, **kwargs)
    except Exception:
        instance._datadog_queries.pop()
        raise


async def traced_receive_messages(func, instance, args, kwargs):
    """Trace ClaudeSDKClient.receive_messages() - creates a span for each query-response pair."""
    from claude_agent_sdk.types import ResultMessage

    integration = claude_agent_sdk._datadog_integration
    span = None
    query_args, query_kwargs = None, None
    response_messages = []

    try:
        async for message in func(*args, **kwargs):
            # Start a new span for each query
            if span is None and hasattr(instance, "_datadog_queries") and instance._datadog_queries:
                query_args, query_kwargs = instance._datadog_queries.pop(0)
                span = _create_query_span(integration, "claude_agent_sdk.client.query", instance)
                response_messages = []

            response_messages.append(message)
            yield message

            # Finish span when we receive a ResultMessage
            if isinstance(message, ResultMessage) and span is not None:
                integration.llmobs_set_tags(
                    span, args=query_args, kwargs=query_kwargs, response=response_messages, operation="query"
                )
                span.finish()
                span.duration = message.duration_api_ms / 1000.0
                span = None
                response_messages = []
    except Exception:
        if span is not None:
            span.set_exc_info(*sys.exc_info())
            span.finish()
        raise


def patch():
    """Activate tracing for claude_agent_sdk."""
    if getattr(claude_agent_sdk, "_datadog_patch", False):
        return

    claude_agent_sdk._datadog_patch = True

    integration = ClaudeAgentSdkIntegration(integration_config=config.claude_agent_sdk)
    claude_agent_sdk._datadog_integration = integration

    wrap("claude_agent_sdk", "query", traced_query)
    wrap("claude_agent_sdk", "ClaudeSDKClient.query", traced_client_query)
    wrap("claude_agent_sdk", "ClaudeSDKClient.receive_messages", traced_receive_messages)


def unpatch():
    """Disable tracing for claude_agent_sdk."""
    if not getattr(claude_agent_sdk, "_datadog_patch", False):
        return

    claude_agent_sdk._datadog_patch = False

    unwrap(claude_agent_sdk, "query")
    unwrap(claude_agent_sdk.ClaudeSDKClient, "query")
    unwrap(claude_agent_sdk.ClaudeSDKClient, "receive_messages")
