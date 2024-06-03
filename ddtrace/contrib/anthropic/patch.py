import json
import os
import sys
from typing import Any

import anthropic

from ddtrace import config
from ddtrace.contrib.trace_utils import unwrap
from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.contrib.trace_utils import wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value
from ddtrace.llmobs._integrations import AnthropicIntegration
from ddtrace.pin import Pin

from .utils import _get_attr
from .utils import record_usage


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


def _extract_api_key(instance: Any) -> str:
    """
    Extract and format LLM-provider API key from instance.
    """
    client = getattr(instance, "_client", "")
    if client:
        return getattr(client, "api_key", None)
    return None


@with_traced_module
def traced_chat_model_generate(anthropic, pin, func, instance, args, kwargs):
    chat_messages = get_argument_value(args, kwargs, 0, "messages")
    integration = anthropic._datadog_integration

    operation_name = func.__name__

    span = integration.trace(
        pin,
        "%s.%s.%s" % (instance.__module__, instance.__class__.__name__, operation_name),
        submit_to_llmobs=True,
        interface_type="chat_model",
        provider="anthropic",
        model=kwargs.get("model", ""),
        api_key=_extract_api_key(instance),
    )

    chat_completions = None
    try:
        for message_idx, message in enumerate(chat_messages):
            if isinstance(message, dict):
                if isinstance(message.get("content", None), str):
                    if integration.is_pc_sampled_span(span) and message.get("content", "") != "":
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
                            if block.get("type", None) == "text" and block.get("text", "") != "":
                                span.set_tag_str(
                                    "anthropic.request.messages.%d.content.%d.text" % (message_idx, block_idx),
                                    integration.trunc(str(block.get("text", ""))),
                                )
                            elif block.get("type", None) == "image":
                                span.set_tag_str(
                                    "anthropic.request.messages.%d.content.%d.text" % (message_idx, block_idx),
                                    "([IMAGE DETECTED])",
                                )

                        span.set_tag_str(
                            "anthropic.request.messages.%d.content.%d.type" % (message_idx, block_idx),
                            block.get("type", "text"),
                        )
                span.set_tag_str(
                    "anthropic.request.messages.%d.role" % (message_idx),
                    message.get("role", ""),
                )
        params_to_tag = {k: v for k, v in kwargs.items() if k != "messages"}
        span.set_tag_str("anthropic.request.parameters", json.dumps(params_to_tag))

        chat_completions = func(*args, **kwargs)

        handle_non_streamed_response(integration, chat_completions, args, kwargs, span)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise
    finally:
        span.finish()
    return chat_completions


def handle_non_streamed_response(integration, chat_completions, args, kwargs, span):
    for idx, chat_completion in enumerate(chat_completions.content):
        if integration.is_pc_sampled_span(span) and getattr(chat_completion, "text", "") != "":
            span.set_tag_str(
                "anthropic.response.completions.content.%d.text" % (idx),
                integration.trunc(str(getattr(chat_completion, "text", ""))),
            )
        span.set_tag_str(
            "anthropic.response.completions.content.%d.type" % (idx),
            chat_completion.type,
        )

    # set message level tags
    if getattr(chat_completions, "stop_reason", None) is not None:
        span.set_tag_str("anthropic.response.completions.finish_reason", chat_completions.stop_reason)
    span.set_tag_str("anthropic.response.completions.role", chat_completions.role)

    usage = _get_attr(chat_completions, "usage", {})
    record_usage(span, usage)


def patch():
    if getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = True

    Pin().onto(anthropic)
    integration = AnthropicIntegration(integration_config=config.anthropic)
    anthropic._datadog_integration = integration

    wrap("anthropic", "resources.messages.Messages.create", traced_chat_model_generate(anthropic))


def unpatch():
    if not getattr(anthropic, "_datadog_patch", False):
        return

    anthropic._datadog_patch = False

    unwrap(anthropic.resources.messages.Messages, "create")

    delattr(anthropic, "_datadog_integration")
