import json
import sys

from ddtrace.contrib.trace_utils import with_traced_module
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils import get_argument_value

from .utils import _extract_api_key
from .utils import handle_non_streamed_response


log = get_logger(__name__)


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
                        if block.get("type", None) == "text":
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

        chat_completions = await func(*args, **kwargs)

        if isinstance(chat_completions, anthropic.AsyncStream) or isinstance(
            chat_completions, anthropic.lib.streaming._messages.AsyncMessageStreamManager
        ):
            pass
        else:
            handle_non_streamed_response(integration, chat_completions, args, kwargs, span)
    except Exception:
        span.set_exc_info(*sys.exc_info())
        span.finish()
        raise
    finally:
        span.finish()
    return chat_completions
