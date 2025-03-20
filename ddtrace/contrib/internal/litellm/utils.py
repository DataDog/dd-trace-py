import sys
from ddtrace.llmobs._utils import _get_attr
from typing import List, Any, Dict

# TODO: temporary since we may want to intercept get_llm_provider response
def get_provider(model):
    parsed_model = model.split("/")
    if len(parsed_model) == 2:
        return parsed_model[0]
    else:
        return ""
    
def tag_request(span, integration, kwargs):
    """Tag the completion span with request details.
    """
    span.set_tag_str("litellm.request.api_base", kwargs.get("api_base", ""))
    messages = kwargs.get("messages", None)
    if messages:
        for i, message in enumerate(messages):
            content = message.get("content")
            role = message.get("role")
            span.set_tag_str("litellm.request.contents.%d.text" % i, str(content))
            span.set_tag_str("litellm.request.contents.%d.role" % i, str(role))

    tag_request_metadata(span, kwargs)

    stream = kwargs.get("stream", None)
    if stream:
        span.set_tag("litellm.request.stream", True)

    if not integration.is_pc_sampled_span(span):
        return

def tag_request_metadata(span, kwargs):
    temperature = kwargs.get("temperature", None)
    max_tokens = kwargs.get("max_tokens", None)
    if temperature:
        span.set_tag_str("litellm.request.metadata.temperature", str(temperature))
    if max_tokens:
        span.set_tag_str("litellm.request.metadata.max_tokens", str(max_tokens))

def tag_response(span, generations, integration):
    # convert non-streamed response to the same format as streamed responses for consistent tagging
    choices = _get_attr(generations, "choices", [])
    formatted_choices = []
    for choice in choices:
        formatted_choice = {}
        text = _get_attr(choice, "text", "")
        if text:
            formatted_choice["text"] = text
        message = _get_attr(choice, "message", None)
        if message:
            message_role = _get_attr(message, "role", "")
            if message_role:
                formatted_choice["role"] = message_role
            message_content = _get_attr(message, "content", "")
            if message_content:
                formatted_choice["content"] = message_content
            tool_calls = _get_attr(message, "tool_calls", [])
            if tool_calls:
                formatted_choice["tool_calls"] = tool_calls
        finish_reason = _get_attr(choice, "finish_reason", "")
        if finish_reason:
            formatted_choice["finish_reason"] = finish_reason
        formatted_choices.append(formatted_choice)

    if integration.is_pc_sampled_span(span):
        _tag_response(integration, span, formatted_choices)
    
    # set token metrics and model tag
    usage = _get_attr(generations, "usage", {})
    _set_token_metrics(span, usage)
    model = _get_attr(generations, "model", None)
    if model:
        span.set_tag_str("litellm.response.model", str(model))


class BaseTracedLiteLLMStreamResponse:
    def __init__(self, generator, integration, span, args, kwargs, is_completion=False):
        n = kwargs.get("n", 1) or 1
        self._generator = generator
        self._dd_integration = integration
        self._dd_span = span
        self._args = args
        self._kwargs = kwargs
        self._streamed_chunks = [[] for _ in range(n)]
        self._is_completion = is_completion

class TracedAsyncLiteLLMStreamResponse(BaseTracedLiteLLMStreamResponse):
    async def __aenter__(self):
        self._generator.__enter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self._generator.__exit__(exc_type, exc_val, exc_tb)

    async def __aiter__(self):
        try:
            async for chunk in self._generator.__aiter__():
                yield chunk
                _loop_handler(self._dd_span, chunk, self._streamed_chunks)
        except Exception:
            self._dd_span.set_exc_info(*sys.exc_info())
            raise
        finally:
            _process_finished_stream(self._dd_integration, self._dd_span, self._streamed_chunks, self._is_completion)
            self._dd_span.finish()

def _loop_handler(span, chunk, streamed_chunks):
    """Sets the model tag and appends the chunk to the correct index in the streamed_chunks list.

    When handling a streamed chat/completion response, this function is called for each chunk in the streamed response.
    """
    if span.get_tag("litellm.response.model") is None:
        span.set_tag("litellm.response.model", chunk.model)
    for choice in chunk.choices:
        streamed_chunks[choice.index].append(choice)
    if getattr(chunk, "usage", None):
        streamed_chunks[0].insert(0, chunk)


def _process_finished_stream(integration, span, streamed_chunks, is_completion=False):
    try:
        if is_completion:
            formatted_completions = [_construct_completion_from_streamed_chunks(choice) for choice in streamed_chunks]
        else:
            role = "" # role is only defined on the very first chunk with content
            if streamed_chunks and len(streamed_chunks[0]) > 1:
                delta = _get_attr(streamed_chunks[0][1], "delta", None) # skip usage chunk in the first position
                role = _get_attr(delta, "role", None) or role
            formatted_completions = [_construct_message_from_streamed_chunks(choice, role) for choice in streamed_chunks]
        if integration.is_pc_sampled_span(span):
            _tag_response(integration, span, formatted_completions)
        _set_token_metrics(span, formatted_completions[0].get("usage", {}))
        if streamed_chunks and streamed_chunks[0]:
            model = getattr(streamed_chunks[0][0], "model", None)
            if model:
                span.set_tag_str("litellm.response.model", str(model))
    except Exception:
        pass


def _construct_completion_from_streamed_chunks(streamed_chunks: List[Any]) -> Dict[str, str]:
    """Constructs a completion dictionary of form {"text": "...", "finish_reason": "..."} from streamed chunks."""
    if not streamed_chunks:
        return {"text": ""}
    completion = {"text": "".join(c.text for c in streamed_chunks if getattr(c, "text", None))}
    if streamed_chunks[-1].finish_reason is not None:
        completion["finish_reason"] = streamed_chunks[-1].finish_reason
    if hasattr(streamed_chunks[0], "usage"):
        completion["usage"] = streamed_chunks[0].usage
    return completion


def _construct_tool_call_from_streamed_chunk(stored_tool_calls, tool_call_chunk=None, function_call_chunk=None):
    """Builds a tool_call dictionary from streamed function_call/tool_call chunks."""
    if function_call_chunk:
        if not stored_tool_calls:
            stored_tool_calls.append({"name": getattr(function_call_chunk, "name", ""), "arguments": ""})
        stored_tool_calls[0]["arguments"] += getattr(function_call_chunk, "arguments", "")
        return
    if not tool_call_chunk:
        return
    tool_call_idx = getattr(tool_call_chunk, "index", None)
    tool_id = getattr(tool_call_chunk, "id", None)
    tool_type = getattr(tool_call_chunk, "type", None)
    function_call = getattr(tool_call_chunk, "function", None)
    function_name = getattr(function_call, "name", "")
    # Find tool call index in tool_calls list, as it may potentially arrive unordered (i.e. index 2 before 0)
    list_idx = next(
        (idx for idx, tool_call in enumerate(stored_tool_calls) if tool_call["index"] == tool_call_idx),
        None,
    )
    if list_idx is None:
        stored_tool_calls.append(
            {"name": function_name, "arguments": "", "index": tool_call_idx, "tool_id": tool_id, "type": tool_type}
        )
        list_idx = -1
    stored_tool_calls[list_idx]["arguments"] += getattr(function_call, "arguments", "")


def _construct_message_from_streamed_chunks(streamed_chunks: List[Any], role: str) -> Dict[str, str]:
    """Constructs a chat completion message dictionary from streamed chunks.
    The resulting message dictionary is of form:
    {"content": "...", "role": "...", "tool_calls": [...], "finish_reason": "..."}
    """
    message = {"content": "", "tool_calls": [], "role": role}
    for chunk in streamed_chunks:
        if getattr(chunk, "usage", None):
            message["usage"] = chunk.usage
        if not hasattr(chunk, "delta"):
            continue
        if getattr(chunk, "index", None) and not message.get("index"):
            message["index"] = chunk.index
        if getattr(chunk, "finish_reason", None) and not message.get("finish_reason"):
            message["finish_reason"] = chunk.finish_reason
        chunk_content = getattr(chunk.delta, "content", "")
        if chunk_content:
            message["content"] += chunk_content
            continue
        function_call = getattr(chunk.delta, "function_call", None)
        if function_call:
            _construct_tool_call_from_streamed_chunk(message["tool_calls"], function_call_chunk=function_call)
        tool_calls = getattr(chunk.delta, "tool_calls", None)
        if not tool_calls:
            continue
        for tool_call in tool_calls:
            _construct_tool_call_from_streamed_chunk(message["tool_calls"], tool_call_chunk=tool_call)
    if message["tool_calls"]:
        message["tool_calls"].sort(key=lambda x: x.get("index", 0))
    else:
        message.pop("tool_calls", None)
    message["content"] = message["content"].strip()
    return message


def _tag_response(integration, span, completions_or_messages=None):
    """Tagging logic for streamed completions and chat completions."""
    for idx, choice in enumerate(completions_or_messages):
        text = choice.get("text", "")
        if text:
            span.set_tag_str("litellm.response.choices.%d.text" % idx, integration.trunc(str(text)))
        message_role = choice.get("role", "")
        if message_role:
            span.set_tag_str("litellm.response.choices.%d.message.role" % idx, str(message_role))
        message_content = choice.get("content", "")
        if message_content:
            span.set_tag_str(
                "litellm.response.choices.%d.message.content" % idx, integration.trunc(str(message_content))
            )
        tool_calls = choice.get("tool_calls", [])
        if tool_calls:
            _tag_tool_calls(integration, span, tool_calls, idx)
        finish_reason = choice.get("finish_reason", "")
        if finish_reason:
            span.set_tag_str("litellm.response.choices.%d.finish_reason" % idx, str(finish_reason))


def _set_token_metrics(span, usage):
    """Set token span metrics.
    """
    span.set_metric("litellm.response.usage.prompt_tokens", _get_attr(usage, "prompt_tokens", 0))
    span.set_metric(
        "litellm.response.usage.completion_tokens", _get_attr(usage, "completion_tokens", 0)
    )
    span.set_metric("litellm.response.usage.total_tokens", _get_attr(usage, "total_tokens", 0))

def _tag_tool_calls(integration, span, tool_calls, choice_idx):
    # type: (...) -> None
    """
    Tagging logic if function_call or tool_calls are provided in the chat response.
    Notes:
        - since function calls are deprecated and will be replaced with tool calls, apply the same tagging logic/schema.
        - streamed responses are processed and collected as dictionaries rather than objects,
          so we need to handle both ways of accessing values.
    """
    for idy, tool_call in enumerate(tool_calls):
        if hasattr(tool_call, "function"):
            # tool_call is further nested in a "function" object
            tool_call = tool_call.function
        function_arguments = _get_attr(tool_call, "arguments", "")
        function_name = _get_attr(tool_call, "name", "")
        span.set_tag_str(
            "litellm.response.choices.%d.message.tool_calls.%d.arguments" % (choice_idx, idy),
            integration.trunc(str(function_arguments)),
        )
        span.set_tag_str(
            "litellm.response.choices.%d.message.tool_calls.%d.name" % (choice_idx, idy), str(function_name)
        )

