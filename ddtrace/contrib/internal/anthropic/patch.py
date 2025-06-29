import os
import sys
from typing import Dict

import anthropic

from ddtrace import config
from ddtrace.contrib.internal.anthropic._streaming import handle_streamed_response
from ddtrace.contrib.internal.anthropic._streaming import is_streaming_operation
from ddtrace.contrib.internal.anthropic.utils import _extract_api_key
from ddtrace.contrib.internal.anthropic.utils import handle_non_streamed_response
from ddtrace.contrib.internal.anthropic.utils import tag_params_on_span
from ddtrace.contrib.internal.anthropic.utils import tag_tool_result_input_on_span
from ddtrace.contrib.internal.anthropic.utils import tag_tool_use_input_on_span
from ddtrace.contrib.internal.trace_utils import unwrap
from ddtrace.contrib.internal.trace_utils import with_traced_module
from ddtrace.contrib.internal.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations import AnthropicIntegration
from ddtrace.llmobs._utils import _get_attr
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
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration = anthropic._datadog_integration
    stream = False

    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="anthropic",
        model=kwargs.get("model", ""),
        api_key=_extract_api_key(instance),
        instance=instance,
    )

    chat_completions = None
    try:
        for message_idx, message in enumerate(chat_messages):
            if not isinstance(message, dict):
                continue
            if isinstance(message.get("content", None), str):
                if integration.is_pc_sampled_span(span):
                    span.set_tag_str(
                        "anthropic.request.messages.%d.content.0.text" % message_idx,
                        integration.trunc(str(message.get("content", ""))),
                    )
                span.set_tag_str("anthropic.request.messages.%d.content.0.type" % message_idx, "text")
            elif isinstance(message.get("content", None), list):
                for block_idx, block in enumerate(message.get("content", [])):
                    if integration.is_pc_sampled_span(span):
                        if _get_attr(block, "type", None) == "text":
                            span.set_tag_str(
                                "anthropic.request.messages.%d.content.%d.text" % (message_idx, block_idx),
                                integration.trunc(str(_get_attr(block, "text", ""))),
                            )
                        elif _get_attr(block, "type", None) == "image":
                            span.set_tag_str(
                                "anthropic.request.messages.%d.content.%d.text" % (message_idx, block_idx),
                                "([IMAGE DETECTED])",
                            )
                        elif _get_attr(block, "type", None) == "tool_use":
                            tag_tool_use_input_on_span(integration, span, block, message_idx, block_idx)

                        elif _get_attr(block, "type", None) == "tool_result":
                            tag_tool_result_input_on_span(integration, span, block, message_idx, block_idx)

                    span.set_tag_str(
                        "anthropic.request.messages.%d.content.%d.type" % (message_idx, block_idx),
                        str(_get_attr(block, "type", "text")),
                    )
            span.set_tag_str("anthropic.request.messages.%d.role" % message_idx, str(message.get("role", "")))
        tag_params_on_span(span, kwargs, integration)

        chat_completions = func(*args, **kwargs)

        if is_streaming_operation(chat_completions):
            stream = True
            return handle_streamed_response(integration, chat_completions, args, kwargs, span)
        else:
            handle_non_streamed_response(integration, chat_completions, args, kwargs, span)
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
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration = anthropic._datadog_integration
    stream = False

    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, func.__name__),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="anthropic",
        model=kwargs.get("model", ""),
        api_key=_extract_api_key(instance),
        instance=instance,
    )

    chat_completions = None
    try:
        for message_idx, message in enumerate(chat_messages):
            if not isinstance(message, dict):
                continue
            if isinstance(message.get("content", None), str):
                if integration.is_pc_sampled_span(span):
                    span.set_tag_str(
                        "anthropic.request.messages.%d.content.0.text" % message_idx,
                        integration.trunc(str(message.get("content", ""))),
                    )
                span.set_tag_str("anthropic.request.messages.%d.content.0.type" % message_idx, "text")
            elif isinstance(message.get("content", None), list):
                for block_idx, block in enumerate(message.get("content", [])):
                    if integration.is_pc_sampled_span(span):
                        if _get_attr(block, "type", None) == "text":
                            span.set_tag_str(
                                "anthropic.request.messages.%d.content.%d.text" % (message_idx, block_idx),
                                integration.trunc(str(_get_attr(block, "text", ""))),
                            )
                        elif _get_attr(block, "type", None) == "image":
                            span.set_tag_str(
                                "anthropic.request.messages.%d.content.%d.text" % (message_idx, block_idx),
                                "([IMAGE DETECTED])",
                            )
                        elif _get_attr(block, "type", None) == "tool_use":
                            tag_tool_use_input_on_span(integration, span, block, message_idx, block_idx)

                        elif _get_attr(block, "type", None) == "tool_result":
                            tag_tool_result_input_on_span(integration, span, block, message_idx, block_idx)

                    span.set_tag_str(
                        "anthropic.request.messages.%d.content.%d.type" % (message_idx, block_idx),
                        str(_get_attr(block, "type", "text")),
                    )
            span.set_tag_str("anthropic.request.messages.%d.role" % message_idx, str(message.get("role", "")))
        tag_params_on_span(span, kwargs, integration)

        chat_completions = await func(*args, **kwargs)

        if is_streaming_operation(chat_completions):
            stream = True
            return handle_streamed_response(integration, chat_completions, args, kwargs, span)
        else:
            handle_non_streamed_response(integration, chat_completions, args, kwargs, span)
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
