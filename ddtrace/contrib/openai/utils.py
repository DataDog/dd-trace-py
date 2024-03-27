import re
from typing import Any
from typing import AsyncGenerator
from typing import Dict
from typing import Generator
from typing import List

from ddtrace.internal.logger import get_logger


try:
    from tiktoken import encoding_for_model

    tiktoken_available = True
except ModuleNotFoundError:
    tiktoken_available = False


log = get_logger(__name__)

_punc_regex = re.compile(r"[\w']+|[.,!?;~@#$%^&*()+/-]")


def _compute_token_count(content, model):
    # type: (Union[str, List[int]], Optional[str]) -> Tuple[bool, int]
    """
    Takes in prompt/response(s) and model pair, and returns a tuple of whether or not the number of prompt
    tokens was estimated, and the estimated/calculated prompt token count.
    """
    num_prompt_tokens = 0
    estimated = False
    if model is not None and tiktoken_available is True:
        try:
            enc = encoding_for_model(model)
            if isinstance(content, str):
                num_prompt_tokens += len(enc.encode(content))
            elif isinstance(content, list) and isinstance(content[0], int):
                num_prompt_tokens += len(content)
            return estimated, num_prompt_tokens
        except KeyError:
            # tiktoken.encoding_for_model() will raise a KeyError if it doesn't have a tokenizer for the model
            estimated = True
    else:
        estimated = True

    # If model is unavailable or tiktoken is not imported, then provide a very rough estimate of the number of tokens
    return estimated, _est_tokens(content)


def _est_tokens(prompt):
    # type: (Union[str, List[int]]) -> int
    """
    Provide a very rough estimate of the number of tokens in a string prompt.
    Note that if the prompt is passed in as a token array (list of ints), the token count
    is just the length of the token array.
    """
    # If model is unavailable or tiktoken is not imported, then provide a very rough estimate of the number of tokens
    # Approximate using the following assumptions:
    #    * English text
    #    * 1 token ~= 4 chars
    #    * 1 token ~= ¾ words
    est_tokens = 0
    if isinstance(prompt, str):
        est1 = len(prompt) / 4
        est2 = len(_punc_regex.findall(prompt)) * 0.75
        return round((1.5 * est1 + 0.5 * est2) / 2)
    elif isinstance(prompt, list) and isinstance(prompt[0], int):
        return len(prompt)
    return est_tokens


def _format_openai_api_key(openai_api_key):
    # type: (Optional[str]) -> Optional[str]
    """
    Returns `sk-...XXXX`, where XXXX is the last 4 characters of the provided OpenAI API key.
    This mimics how OpenAI UI formats the API key.
    """
    if not openai_api_key:
        return None
    return "sk-...%s" % openai_api_key[-4:]


def _is_generator(resp):
    # type: (...) -> bool
    import openai

    # In OpenAI v1, the response is type `openai.Stream` instead of Generator.
    if isinstance(resp, Generator):
        return True
    if hasattr(openai, "Stream") and isinstance(resp, openai.Stream):
        return True
    return False


def _is_async_generator(resp):
    # type: (...) -> bool
    import openai

    # In OpenAI v1, the response is type `openai.AsyncStream` instead of AsyncGenerator.
    if isinstance(resp, AsyncGenerator):
        return True
    if hasattr(openai, "AsyncStream") and isinstance(resp, openai.AsyncStream):
        return True
    return False


def _construct_completion_from_streamed_chunks(streamed_chunks: List[Any]) -> Dict[str, str]:
    """Constructs a completion dictionary of form {"text": "...", "finish_reason": "..."} from streamed chunks."""
    completion = {"text": "".join(c.text for c in streamed_chunks if getattr(c, "text", None))}
    if streamed_chunks[-1].finish_reason is not None:
        completion["finish_reason"] = streamed_chunks[-1].finish_reason
    return completion


def _construct_message_from_streamed_chunks(streamed_chunks: List[Any]) -> Dict[str, str]:
    """Constructs a chat completion message dictionary from streamed chunks.
    The resulting message dictionary is of form {"content": "...", "role": "...", "finish_reason": "..."}
    """
    message = {}
    content = ""
    for chunk in streamed_chunks:
        chunk_content = getattr(chunk.delta, "content", "")
        if chunk_content:
            content += chunk_content
        elif getattr(chunk.delta, "function_call", None):
            content += chunk.delta.function_call.arguments
        elif getattr(chunk.delta, "tool_calls", None):
            for tool_call in chunk.delta.tool_calls:
                content += tool_call.function.arguments

    message["role"] = streamed_chunks[0].delta.role or "assistant"
    if streamed_chunks[-1].finish_reason is not None:
        message["finish_reason"] = streamed_chunks[-1].finish_reason
    message["content"] = content
    return message


def _tag_streamed_completion_response(integration, span, completions):
    """Tagging logic for streamed completions."""
    if completions is None:
        return
    for idx, choice in enumerate(completions):
        span.set_tag_str("openai.response.choices.%d.text" % idx, integration.trunc(choice["text"]))
        if choice.get("finish_reason") is not None:
            span.set_tag_str("openai.response.choices.%d.finish_reason" % idx, choice["finish_reason"])


def _tag_streamed_chat_completion_response(integration, span, messages):
    """Tagging logic for streamed chat completions."""
    if messages is None:
        return
    for idx, message in enumerate(messages):
        span.set_tag_str("openai.response.choices.%d.message.content" % idx, integration.trunc(message["content"]))
        span.set_tag_str("openai.response.choices.%d.message.role" % idx, message["role"])
        if message.get("finish_reason") is not None:
            span.set_tag_str("openai.response.choices.%d.finish_reason" % idx, message["finish_reason"])


def _set_metrics_on_request(integration, span, kwargs, prompts=None, messages=None):
    """Set token span metrics on streamed chat/completion requests."""
    num_prompt_tokens = 0
    estimated = False
    if messages is not None:
        for m in messages:
            estimated, prompt_tokens = _compute_token_count(m.get("content", ""), kwargs.get("model"))
            num_prompt_tokens += prompt_tokens
    elif prompts is not None:
        if isinstance(prompts, str) or isinstance(prompts, list) and isinstance(prompts[0], int):
            prompts = [prompts]
        for prompt in prompts:
            estimated, prompt_tokens = _compute_token_count(prompt, kwargs.get("model"))
            num_prompt_tokens += prompt_tokens
    span.set_metric("openai.request.prompt_tokens_estimated", int(estimated))
    span.set_metric("openai.response.usage.prompt_tokens", num_prompt_tokens)
    if not estimated:
        integration.metric(span, "dist", "tokens.prompt", num_prompt_tokens)
    else:
        integration.metric(span, "dist", "tokens.prompt", num_prompt_tokens, tags=["openai.estimated:true"])


def _set_metrics_on_streamed_response(integration, span, completions=None, messages=None):
    """Set token span metrics on streamed chat/completion responses."""
    num_completion_tokens = 0
    estimated = False
    if messages is not None:
        for m in messages:
            estimated, completion_tokens = _compute_token_count(
                m.get("content", ""), span.get_tag("openai.response.model")
            )
            num_completion_tokens += completion_tokens
    elif completions is not None:
        for c in completions:
            estimated, completion_tokens = _compute_token_count(
                c.get("text", ""), span.get_tag("openai.response.model")
            )
            num_completion_tokens += completion_tokens
    span.set_metric("openai.response.completion_tokens_estimated", int(estimated))
    span.set_metric("openai.response.usage.completion_tokens", num_completion_tokens)
    num_prompt_tokens = span.get_metric("openai.response.usage.prompt_tokens") or 0
    total_tokens = num_prompt_tokens + num_completion_tokens
    span.set_metric("openai.response.usage.total_tokens", total_tokens)
    if not estimated:
        integration.metric(span, "dist", "tokens.completion", num_completion_tokens)
        integration.metric(span, "dist", "tokens.total", total_tokens)
    else:
        integration.metric(span, "dist", "tokens.completion", num_completion_tokens, tags=["openai.estimated:true"])
        integration.metric(span, "dist", "tokens.total", total_tokens, tags=["openai.estimated:true"])


def _loop_handler(span, chunk, streamed_chunks):
    """Sets the openai model tag and appends the chunk to the correct index in the streamed_chunks list.

    When handling a streamed chat/completion response, this function is called for each chunk in the streamed response.
    """
    if span.get_tag("openai.response.model") is None:
        span.set_tag("openai.response.model", chunk.model)
    for choice in chunk.choices:
        streamed_chunks[choice.index].append(choice)


def _tag_tool_calls(integration, span, tool_calls, choice_idx):
    # type: (...) -> None
    """
    Tagging logic if function_call or tool_calls are provided in the chat response.
    Note: since function calls are deprecated and will be replaced with tool calls, apply the same tagging logic/schema.
    """
    for idy, tool_call in enumerate(tool_calls):
        if hasattr(tool_call, "function"):
            # tool_call is further nested in a "function" object
            tool_call = tool_call.function
        span.set_tag(
            "openai.response.choices.%d.message.tool_calls.%d.arguments" % (choice_idx, idy),
            integration.trunc(str(tool_call.arguments)),
        )
        span.set_tag("openai.response.choices.%d.message.tool_calls.%d.name" % (choice_idx, idy), str(tool_call.name))
