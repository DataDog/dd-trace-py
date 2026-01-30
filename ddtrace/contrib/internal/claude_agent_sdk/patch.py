import sys

import claude_agent_sdk

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.claude_agent_sdk._streaming import handle_query_stream
from ddtrace.contrib.internal.claude_agent_sdk._streaming import handle_receive_messages_stream
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


@with_traced_module
def traced_query_async_generator(claude_agent_sdk, pin, func, _instance, args, kwargs):
    """Trace the standalone query() async generator function."""
    integration = claude_agent_sdk._datadog_integration
    return handle_query_stream(
        integration=integration,
        pin=pin,
        func=func,
        args=args,
        kwargs=kwargs,
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
        instance._datadog_query_args = None
        instance._datadog_query_kwargs = None
        raise


@with_traced_module
def traced_receive_messages(claude_agent_sdk, _pin, func, instance, args, kwargs):
    """Trace ClaudeSDKClient.receive_messages() - closes span started by query()."""
    integration = claude_agent_sdk._datadog_integration

    # Get span started by query(), if any
    span = getattr(instance, "_datadog_span", None)
    if not span:
        # No span to trace, return unwrapped generator
        return func(*args, **kwargs)

    query_args = getattr(instance, "_datadog_query_args", ())
    query_kwargs = getattr(instance, "_datadog_query_kwargs", {})

    try:
        agen = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        integration.llmobs_set_tags(
            span, args=query_args, kwargs=query_kwargs, response=None, operation="request"
        )
        span.finish()
        instance._datadog_span = None
        instance._datadog_query_args = None
        instance._datadog_query_kwargs = None
        raise

    return handle_receive_messages_stream(
        integration=integration,
        instance=instance,
        agen=agen,
        span=span,
        query_args=query_args,
        query_kwargs=query_kwargs,
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
