import os
import sys
from typing import Dict

import anthropic

from ddtrace import config
from ddtrace.contrib.internal.anthropic._streaming import handle_streamed_response
from ddtrace.contrib.internal.anthropic._streaming import is_streaming_operation
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._integrations import AnthropicIntegration
from ddtrace.trace import Pin


log = get_logger(__name__)


def get_version():
    # type: () -> str
    return getattr(anthropic, "__version__", "")


def _supported_versions() -> Dict[str, str]:
    return {"anthropic": ">=0.28.0"}


config._add(
    "anthropic",
    {
        "span_prompt_completion_sample_rate": float(os.getenv("DD_ANTHROPIC_SPAN_PROMPT_COMPLETION_SAMPLE_RATE", 1.0)),
        "span_char_limit": int(os.getenv("DD_ANTHROPIC_SPAN_CHAR_LIMIT", 128)),
    },
)


@with_traced_module
def traced_chat_model_generate(anthropic, pin, func, instance, args, kwargs):
    integration = anthropic._datadog_integration
    stream = False

    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="anthropic",
        model=kwargs.get("model", ""),
        instance=instance,
    )

    chat_completions = None
    try:
        chat_completions = func(*args, **kwargs)

        if is_streaming_operation(chat_completions):
            stream = True
            return handle_streamed_response(integration, chat_completions, args, kwargs, span)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # we don't want to finish the span if it is a stream as it will get finished once the iterator is exhausted
        if span.error or not stream:
            integration.llmobs_set_tags(span, args=[], kwargs=kwargs, response=chat_completions)
            span.finish()
    return chat_completions


@with_traced_module
async def traced_async_chat_model_generate(anthropic, pin, func, instance, args, kwargs):
    integration = anthropic._datadog_integration
    stream = False

    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="anthropic",
        model=kwargs.get("model", ""),
        instance=instance,
    )

    chat_completions = None
    try:
        chat_completions = await func(*args, **kwargs)

        if is_streaming_operation(chat_completions):
            stream = True
            return handle_streamed_response(integration, chat_completions, args, kwargs, span)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        # we don't want to finish the span if it is a stream as it will get finished once the iterator is exhausted
        if span.error or not stream:
            integration.llmobs_set_tags(span, args=[], kwargs=kwargs, response=chat_completions)
            span.finish()
    return chat_completions


def patch():
    if getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = True

    Pin().onto(anthropic)
    integration = AnthropicIntegration(integration_config=config.anthropic)
    anthropic._datadog_integration = integration

    wrap("anthropic", "resources.messages.Messages.create", traced_chat_model_generate(anthropic))
    wrap("anthropic", "resources.messages.Messages.stream", traced_chat_model_generate(anthropic))
    wrap("anthropic", "resources.messages.AsyncMessages.create", traced_async_chat_model_generate(anthropic))
    # AsyncMessages.stream is a sync function
    wrap("anthropic", "resources.messages.AsyncMessages.stream", traced_chat_model_generate(anthropic))


def unpatch():
    if not getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = False

    unwrap(anthropic.resources.messages.Messages, "create")
    unwrap(anthropic.resources.messages.Messages, "stream")
    unwrap(anthropic.resources.messages.AsyncMessages, "create")
    unwrap(anthropic.resources.messages.AsyncMessages, "stream")

    delattr(anthropic, "_datadog_integration")
