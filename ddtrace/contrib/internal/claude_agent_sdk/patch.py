import sys
from typing import Dict

import claude_agent_sdk

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import SPAN_KIND
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


def _trace_async_generator(integration, pin, func, args, kwargs, operation_name, span_name, operation, instance=None):
    """Common helper for tracing async generators that yield messages."""

    async def _generator():
        # Create span and underlying generator inside to ensure cleanup if wrapper is abandoned
        span = integration.trace(
            pin,
            operation_name,
            submit_to_llmobs=True,
            span_name=span_name,
            model="",
            instance=instance,
        )

        span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
        span._set_tag_str(net.TARGET_HOST, "api.anthropic.com")

        try:
            agen = func(*args, **kwargs)
        except Exception:
            span.set_exc_info(*sys.exc_info())
            span.finish()
            raise

        response_messages = []
        agen_closed = False
        try:
            async for message in agen:
                response_messages.append(message)
                yield message
        except GeneratorExit:
            # Generator was closed early - clean up underlying generator
            if not agen_closed:
                try:
                    await agen.aclose()
                    agen_closed = True
                except Exception:  # nosec B110
                    pass
            raise
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            # Always close underlying generator and finish span
            if not agen_closed:
                try:
                    await agen.aclose()
                except Exception:  # nosec B110
                    pass
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response_messages, operation=operation)
            span.finish()

    return _generator()


@with_traced_module
def traced_query_async_generator(claude_agent_sdk, pin, func, _instance, args, kwargs):
    """Trace the standalone query() async generator function."""
    integration = claude_agent_sdk._datadog_integration
    return _trace_async_generator(
        integration,
        pin,
        func,
        args,
        kwargs,
        operation_name="claude_agent_sdk.query",
        span_name="claude_agent_sdk.query",
        operation="query",
    )


@with_traced_module
async def traced_client_query(claude_agent_sdk, pin, func, instance, args, kwargs):
    """Trace ClaudeSDKClient.query() - creates a span for the query request.

    Since query() just sends a message and returns None (the response comes via
    receive_messages()), we finish the span immediately after the request completes.
    """
    integration = claude_agent_sdk._datadog_integration

    span = integration.trace(
        pin,
        "claude_agent_sdk.client_query",
        submit_to_llmobs=True,
        span_name="claude_agent_sdk.client_query",
        model="",
        instance=instance,
    )

    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span._set_tag_str(net.TARGET_HOST, "api.anthropic.com")

    try:
        result = await func(*args, **kwargs)
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="client_query")
        return result
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="client_query")
        raise
    finally:
        span.finish()


@with_traced_module
def traced_receive_messages(claude_agent_sdk, pin, func, instance, args, kwargs):
    """Trace ClaudeSDKClient.receive_messages() - traces the message receiving stream."""
    integration = claude_agent_sdk._datadog_integration

    return _trace_async_generator(
        integration,
        pin,
        func,
        args,
        kwargs,
        operation_name="claude_agent_sdk.receive_messages",
        span_name="claude_agent_sdk.receive_messages",
        operation="receive_messages",
        instance=instance,
    )


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
