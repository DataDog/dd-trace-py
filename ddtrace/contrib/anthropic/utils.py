import json
from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def handle_non_streamed_response(integration, chat_completions, args, kwargs, span):
    for idx, chat_completion in enumerate(chat_completions.content):
        if integration.is_pc_sampled_span(span):
            if getattr(chat_completion, "text", "") != "":
                span.set_tag_str(
                    "anthropic.response.completions.content.%d.text" % (idx),
                    integration.trunc(str(getattr(chat_completion, "text", ""))),
                )
            elif chat_completion.type == "tool_use":
                tag_tool_usage_on_span(span, chat_completion, idx)

        span.set_tag_str("anthropic.response.completions.content.%d.type" % (idx), chat_completion.type)

    # set message level tags
    if getattr(chat_completions, "stop_reason", None) is not None:
        span.set_tag_str("anthropic.response.completions.finish_reason", chat_completions.stop_reason)
    span.set_tag_str("anthropic.response.completions.role", chat_completions.role)

    usage = _get_attr(chat_completions, "usage", {})
    integration.record_usage(span, usage)


def tag_tool_usage_on_span(span, chat_completion, idx):
    tool_name = _get_attr(chat_completion, "name", None)
    tool_inputs = _get_attr(chat_completion, "input", None)
    if tool_name:
        span.set_tag_str("anthropic.response.completions.content.%d.name" % (idx), tool_name)
    if tool_inputs:
        span.set_tag_str("anthropic.response.completions.content.%d.name" % (idx), json.dumps(tool_inputs))


def tag_params_on_span(span, kwargs, integration):
    tagged_params = {}
    for k, v in kwargs.items():
        if k == "system" and integration.is_pc_sampled_span(span):
            span.set_tag_str("anthropic.request.system", integration.trunc(v))
        elif k not in ("messages", "model", "tools"):
            tagged_params[k] = v
    span.set_tag_str("anthropic.request.parameters", json.dumps(tagged_params))


def _extract_api_key(instance: Any) -> Optional[str]:
    """
    Extract and format LLM-provider API key from instance.
    """
    client = getattr(instance, "_client", "")
    if client:
        return getattr(client, "api_key", None)
    return None


def _get_attr(o: Any, attr: str, default: Any):
    # Since our response may be a dict or object, convenience method
    if isinstance(o, dict):
        return o.get(attr, default)
    else:
        return getattr(o, attr, default)
