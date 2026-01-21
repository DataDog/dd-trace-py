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


def _supported_versions() -> Dict[str, str]:
    return {"claude_agent_sdk": ">=0.1.0"}


def get_version() -> str:
    return getattr(claude_agent_sdk, "__version__", "")


@with_traced_module
def traced_query_async_generator(claude_agent_sdk, pin, func, instance, args, kwargs):
    """Trace the standalone query() async generator function."""
    integration = claude_agent_sdk._datadog_integration

    operation_name = "claude_agent_sdk.query"
    span_name = "claude_agent_sdk.query"

    span = integration.trace(
        pin,
        operation_name,
        submit_to_llmobs=True,
        span_name=span_name,
        model="",  # model not known at call time for this SDK
    )

    # Set peer service precursor tags for PeerServiceProcessor
    # span.kind=client indicates this is an outbound request
    # out.host is used as the peer service identifier
    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span._set_tag_str(net.TARGET_HOST, "api.anthropic.com")

    try:
        agen = func(*args, **kwargs)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise

    async def _generator():
        response_messages = []
        try:
            async for message in agen:
                response_messages.append(message)
                yield message
        except Exception:
            span.set_exc_info(*sys.exc_info())
            raise
        finally:
            integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=response_messages, operation="query")
            span.finish()

    return _generator()


@with_traced_module
async def traced_client_query(claude_agent_sdk, pin, func, instance, args, kwargs):
    """Trace ClaudeSDKClient.query() method - sends a message to the transport."""
    integration = claude_agent_sdk._datadog_integration

    operation_name = "ClaudeSDKClient.query"
    span_name = "claude_agent_sdk.client_query"

    span = integration.trace(
        pin,
        operation_name,
        submit_to_llmobs=True,
        span_name=span_name,
        model="",  # model not known at call time for this SDK
        instance=instance,
    )

    # Set peer service precursor tags for PeerServiceProcessor
    # span.kind=client indicates this is an outbound request
    # out.host is used as the peer service identifier
    span._set_tag_str(SPAN_KIND, SpanKind.CLIENT)
    span._set_tag_str(net.TARGET_HOST, "api.anthropic.com")

    result = None
    try:
        result = await func(*args, **kwargs)
        return result
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # ClaudeSDKClient.query returns None - it just sends the message
        # The prompt is in args[0] or kwargs["prompt"]
        integration.llmobs_set_tags(span, args=args, kwargs=kwargs, response=result, operation="client_query")
        span.finish()


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


def unpatch():
    """Disable tracing for claude_agent_sdk."""
    if not getattr(claude_agent_sdk, "_datadog_patch", False):
        return

    claude_agent_sdk._datadog_patch = False

    unwrap(claude_agent_sdk, "query")
    unwrap(claude_agent_sdk.ClaudeSDKClient, "query")

    delattr(claude_agent_sdk, "_datadog_integration")
