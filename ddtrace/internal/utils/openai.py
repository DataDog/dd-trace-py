"""OpenAI stream reconstruction helpers shared by LLM Observability, AI Guard and the OpenAI-compatible
integrations, kept here so none of them has to import another product to rebuild a streamed message.
"""

from typing import Any


def _get_attr(o: object, attr: str, default: Any) -> Any:
    if isinstance(o, dict):
        return o.get(attr, default)
    return getattr(o, attr, default)


def openai_construct_tool_call_from_streamed_chunk(
    stored_tool_calls: list[dict[str, Any]], tool_call_chunk: Any = None, function_call_chunk: Any = None
) -> None:
    """Builds a tool_call dictionary from streamed function_call/tool_call chunks."""
    if function_call_chunk:
        if not stored_tool_calls:
            stored_tool_calls.append({"name": getattr(function_call_chunk, "name", ""), "arguments": ""})
        # ``arguments`` may be present but None on OpenAI-compatible backends (e.g. DashScope),
        # so coerce to "" — getattr's default only applies when the attribute is absent.
        stored_tool_calls[0]["arguments"] += getattr(function_call_chunk, "arguments", "") or ""
        return
    if not tool_call_chunk:
        return
    tool_call_idx = getattr(tool_call_chunk, "index", None)
    tool_id = getattr(tool_call_chunk, "id", None)
    tool_type = getattr(tool_call_chunk, "type", None)
    function_call = getattr(tool_call_chunk, "function", None)
    custom_call = getattr(tool_call_chunk, "custom", None)
    function_name = getattr(function_call, "name", "") or getattr(custom_call, "name", "")
    # Find tool call index in tool_calls list, as it may potentially arrive unordered (i.e. index 2 before 0)
    list_idx = next(
        (idx for idx, tool_call in enumerate(stored_tool_calls) if tool_call["index"] == tool_call_idx),
        None,
    )
    if list_idx is None:
        call_dict = {
            "index": tool_call_idx,
            "id": tool_id,
            "type": tool_type,
        }
        if function_call:
            call_dict["function"] = {"name": function_name, "arguments": ""}
        elif custom_call:
            call_dict["custom"] = {"name": function_name, "input": ""}
        stored_tool_calls.append(call_dict)
        list_idx = -1
    if function_call:
        stored_tool_calls[list_idx]["function"]["arguments"] += getattr(function_call, "arguments", "") or ""
    elif custom_call:
        stored_tool_calls[list_idx]["custom"]["input"] += getattr(custom_call, "input", "") or ""


def openai_construct_message_from_streamed_chunks(streamed_chunks: list[Any]) -> dict[str, Any]:
    """Constructs a chat completion message dictionary from streamed chunks.
    The resulting message dictionary is of form:
    {"content": "...", "role": "...", "reasoning_content": "...", "tool_calls": [...], "finish_reason": "..."}
    """
    message: dict[str, Any] = {"content": "", "reasoning_content": "", "tool_calls": []}
    for chunk in streamed_chunks:
        if _get_attr(chunk, "usage", None):
            message["usage"] = chunk.usage
        if not _get_attr(chunk, "delta", None):
            continue
        if _get_attr(chunk, "index", None) and not message.get("index"):
            message["index"] = chunk.index
        if _get_attr(chunk.delta, "role", None) and not message.get("role"):
            message["role"] = chunk.delta.role
        if _get_attr(chunk, "finish_reason", None) and not message.get("finish_reason"):
            message["finish_reason"] = chunk.finish_reason
        chunk_reasoning = _get_attr(chunk.delta, "reasoning_content", "")
        if chunk_reasoning:
            message["reasoning_content"] += chunk_reasoning
        chunk_content = _get_attr(chunk.delta, "content", "")
        if chunk_content:
            message["content"] += chunk_content
            continue
        function_call = _get_attr(chunk.delta, "function_call", None)
        if function_call:
            openai_construct_tool_call_from_streamed_chunk(message["tool_calls"], function_call_chunk=function_call)
        tool_calls = _get_attr(chunk.delta, "tool_calls", None)
        if not tool_calls:
            continue
        for tool_call in tool_calls:
            openai_construct_tool_call_from_streamed_chunk(message["tool_calls"], tool_call_chunk=tool_call)
    if message["tool_calls"]:
        message["tool_calls"].sort(key=lambda x: x.get("index", 0))
    else:
        message.pop("tool_calls", None)
    if not message["reasoning_content"]:
        message.pop("reasoning_content", None)
    message["content"] = message["content"].strip()
    return message
