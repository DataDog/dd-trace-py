from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._utils import _get_attr
from ddtrace.llmobs._utils import safe_json


log = get_logger(__name__)


def handle_non_streamed_response(integration, chat_completions, args, kwargs, span):
    for idx, block in enumerate(chat_completions.content):
        if integration.is_pc_sampled_span(span):
            if getattr(block, "text", "") != "":
                span.set_tag_str(
                    "anthropic.response.completions.content.%d.text" % idx,
                    integration.trunc(str(getattr(block, "text", ""))),
                )
            elif block.type == "tool_use":
                tag_tool_use_output_on_span(integration, span, block, idx)

        span.set_tag_str("anthropic.response.completions.content.%d.type" % idx, str(block.type))

    # set message level tags
    if getattr(chat_completions, "stop_reason", None) is not None:
        span.set_tag_str("anthropic.response.completions.finish_reason", str(chat_completions.stop_reason))
    span.set_tag_str("anthropic.response.completions.role", str(chat_completions.role))

    usage = _get_attr(chat_completions, "usage", {})
    integration.record_usage(span, usage)


def tag_tool_use_input_on_span(integration, span, chat_input, message_idx, block_idx):
    span.set_tag_str(
        "anthropic.request.messages.%d.content.%d.tool_call.name" % (message_idx, block_idx),
        str(_get_attr(chat_input, "name", "")),
    )
    span.set_tag_str(
        "anthropic.request.messages.%d.content.%d.tool_call.input" % (message_idx, block_idx),
        integration.trunc(safe_json(_get_attr(chat_input, "input", {}))),
    )


def tag_tool_result_input_on_span(integration, span, chat_input, message_idx, block_idx):
    content = _get_attr(chat_input, "content", None)
    if isinstance(content, str):
        span.set_tag_str(
            "anthropic.request.messages.%d.content.%d.tool_result.content.0.text" % (message_idx, block_idx),
            integration.trunc(str(content)),
        )
    elif isinstance(content, list):
        for tool_block_idx, tool_block in enumerate(content):
            tool_block_type = _get_attr(tool_block, "type", "")
            if tool_block_type == "text":
                tool_block_text = _get_attr(tool_block, "text", "")
                span.set_tag_str(
                    "anthropic.request.messages.%d.content.%d.tool_result.content.%d.text"
                    % (message_idx, block_idx, tool_block_idx),
                    integration.trunc(str(tool_block_text)),
                )
            elif tool_block_type == "image":
                span.set_tag_str(
                    "anthropic.request.messages.%d.content.%d.tool_result.content.%d.text"
                    % (message_idx, block_idx, tool_block_idx),
                    "([IMAGE DETECTED])",
                )
            span.set_tag_str(
                "anthropic.request.messages.%d.content.%d.tool_result.content.%d.type"
                % (message_idx, block_idx, tool_block_idx),
                str(tool_block_type),
            )


def tag_tool_use_output_on_span(integration, span, chat_completion, idx):
    tool_name = _get_attr(chat_completion, "name", None)
    tool_inputs = _get_attr(chat_completion, "input", None)
    if tool_name:
        span.set_tag_str("anthropic.response.completions.content.%d.tool_call.name" % idx, str(tool_name))
    if tool_inputs:
        span.set_tag_str(
            "anthropic.response.completions.content.%d.tool_call.input" % idx, integration.trunc(safe_json(tool_inputs))
        )


def tag_params_on_span(span, kwargs, integration):
    tagged_params = {}
    for k, v in kwargs.items():
        if k == "system" and integration.is_pc_sampled_span(span):
            span.set_tag_str("anthropic.request.system", integration.trunc(str(v)))
        elif k not in ("messages", "model"):
            tagged_params[k] = v
    span.set_tag_str("anthropic.request.parameters", safe_json(tagged_params))


def _extract_api_key(instance: Any) -> Optional[str]:
    """
    Extract and format LLM-provider API key from instance.
    """
    client = getattr(instance, "_client", "")
    if client:
        return getattr(client, "api_key", None)
    return None
