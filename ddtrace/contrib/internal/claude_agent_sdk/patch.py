import sys
from typing import Dict

import claude_agent_sdk
from claude_agent_sdk import ResultMessage

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
    return {"claude-agent-sdk": ">=0.1.0"}


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
    """Trace ClaudeSDKClient.query() - starts span, closed by receive_messages."""
    integration = claude_agent_sdk._datadog_integration

    span = integration.trace(
        pin,
        "claude_agent_sdk.request",
        submit_to_llmobs=True,
        span_name="claude_agent_sdk.request",
        model="",
        instance=instance,
    )

    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span._set_tag_str(net.TARGET_HOST, "api.anthropic.com")

    # Store span and query info on instance for receive_messages to close
    instance._datadog_span = span
    instance._datadog_query_args = args
    instance._datadog_query_kwargs = kwargs

    try:
        return await func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=None, operation="request")
        span.finish()
        instance._datadog_span = None
        raise


@with_traced_module
def traced_receive_messages(claude_agent_sdk, _pin, func, instance, args, kwargs):
    """Trace ClaudeSDKClient.receive_messages() - closes span started by query()."""
    integration = claude_agent_sdk._datadog_integration

    # Get span started by query(), if any
    span = getattr(instance, "_datadog_span", None)
    query_args = getattr(instance, "_datadog_query_args", ())
    query_kwargs = getattr(instance, "_datadog_query_kwargs", {})

    async def _generator():
        # Create underlying generator inside to ensure cleanup if wrapper is abandoned
        try:
            agen = func(*args, **kwargs)
        except Exception:
            if span:
                span.set_exc_info(*sys.exc_info())
                integration.llmobs_set_tags(
                    span, args=query_args, kwargs=query_kwargs, response=None, operation="request"
                )
                span.finish()
                instance._datadog_span = None
            raise

        response_messages = []
        agen_closed = False
        try:
            async for message in agen:
                response_messages.append(message)
                yield message
                # Close span when we receive a ResultMessage
                if span and isinstance(message, ResultMessage):
                    integration.llmobs_set_tags(
                        span, args=query_args, kwargs=query_kwargs, response=response_messages, operation="request"
                    )
                    span.finish()
                    instance._datadog_span = None
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
            if span:
                span.set_exc_info(*sys.exc_info())
            raise
        finally:
            # Always close underlying generator
            if not agen_closed:
                try:
                    await agen.aclose()
                except Exception:  # nosec B110
                    pass
            # Close span if not already closed (e.g., generator exhausted without ResultMessage)
            if getattr(instance, "_datadog_span", None) is span and span is not None:
                integration.llmobs_set_tags(
                    span, args=query_args, kwargs=query_kwargs, response=response_messages, operation="request"
                )
                span.finish()
                instance._datadog_span = None

    return _generator()


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
