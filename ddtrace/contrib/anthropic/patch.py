import json
import os
import sys

import anthropic

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations import AnthropicIntegration
from ddtrace.pin import Pin

from .utils import _extract_api_key
from .utils import _get_attr
from .utils import handle_non_streamed_response


log = get_logger(__name__)


def get_version():
    # type: () -> str
    return getattr(anthropic, "__version__", "")


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

    operation_name = func.__name__

    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, operation_name),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="anthropic",
        model=kwargs.get("model", ""),
        api_key=_extract_api_key(instance),
    )

    chat_completions = None
    try:
        for message_idx, message in enumerate(chat_messages):
            if not isinstance(message, dict):
                continue
            if isinstance(message.get("content", None), str):
                if integration.is_pc_sampled_span(span):
                    span.set_tag_str(
                        "anthropic.request.messages.%d.content.0.text" % (message_idx),
                        integration.trunc(message.get("content", "")),
                    )
                span.set_tag_str(
                    "anthropic.request.messages.%d.content.0.type" % (message_idx),
                    "text",
                )
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

                    span.set_tag_str(
                        "anthropic.request.messages.%d.content.%d.type" % (message_idx, block_idx),
                        _get_attr(block, "type", "text"),
                    )
            span.set_tag_str(
                "anthropic.request.messages.%d.role" % (message_idx),
                message.get("role", ""),
            )
        params_to_tag = {k: v for k, v in kwargs.items() if k not in ["messages", "model", "tools"]}
        span.set_tag_str("anthropic.request.parameters", json.dumps(params_to_tag))

        chat_completions = func(*args, **kwargs)

        if isinstance(chat_completions, anthropic.Stream) or isinstance(
            chat_completions, anthropic.lib.streaming._messages.MessageStreamManager
        ):
            pass
        else:
            handle_non_streamed_response(integration, chat_completions, args, kwargs, span)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()
    return chat_completions


@with_traced_module
async def traced_async_chat_model_generate(anthropic, pin, func, instance, args, kwargs):
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration = anthropic._datadog_integration

    operation_name = func.__name__

    span = integration.trace(
        pin,
        "%s.%s" % (instance.__class__.__name__, operation_name),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="anthropic",
        model=kwargs.get("model", ""),
        api_key=_extract_api_key(instance),
    )

    chat_completions = None
    try:
        for message_idx, message in enumerate(chat_messages):
            if not isinstance(message, dict):
                continue
            if isinstance(message.get("content", None), str):
                if integration.is_pc_sampled_span(span):
                    span.set_tag_str(
                        "anthropic.request.messages.%d.content.0.text" % (message_idx),
                        integration.trunc(message.get("content", "")),
                    )
                span.set_tag_str(
                    "anthropic.request.messages.%d.content.0.type" % (message_idx),
                    "text",
                )
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

                    span.set_tag_str(
                        "anthropic.request.messages.%d.content.%d.type" % (message_idx, block_idx),
                        _get_attr(block, "type", "text"),
                    )
            span.set_tag_str(
                "anthropic.request.messages.%d.role" % (message_idx),
                message.get("role", ""),
            )
        params_to_tag = {k: v for k, v in kwargs.items() if k not in ["messages", "model", "tools"]}
        span.set_tag_str("anthropic.request.parameters", json.dumps(params_to_tag))

        chat_completions = await func(*args, **kwargs)

        if isinstance(chat_completions, anthropic.AsyncStream) or isinstance(
            chat_completions, anthropic.lib.streaming._messages.AsyncMessageStreamManager
        ):
            pass
        else:
            handle_non_streamed_response(integration, chat_completions, args, kwargs, span)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
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
    wrap("anthropic", "resources.messages.AsyncMessages.create", traced_async_chat_model_generate(anthropic))


def unpatch():
    if not getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = False

    unwrap(anthropic.resources.messages.Messages, "create")
    unwrap(anthropic.resources.messages.AsyncMessages, "create")

    delattr(anthropic, "_datadog_integration")
