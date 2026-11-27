import collections
from typing import Any

from ddtrace.internal.utils.openai import openai_construct_message_from_streamed_chunks


def reconstruct_openai_chat(chunks: list[Any]) -> Any:
    """Rebuild a {"choices": [{"message": ...}]} dict from buffered chunks, one message per choice index."""
    by_choice: collections.OrderedDict[int, list[Any]] = collections.OrderedDict()
    for chunk in chunks:
        for choice in getattr(chunk, "choices", None) or []:
            by_choice.setdefault(getattr(choice, "index", 0), []).append(choice)
    return {
        "choices": [
            {"message": openai_construct_message_from_streamed_chunks(choices)} for choices in by_choice.values()
        ]
    }


def _build_output_from_text_deltas(chunks: list[Any]) -> list[Any]:
    """Last resort without a snapshot or output_item.done event: one assistant message from the text deltas."""
    text = "".join(
        str(getattr(c, "delta", ""))
        for c in chunks
        if getattr(c, "type", "") == "response.output_text.delta" and getattr(c, "delta", None)
    )
    if not text:
        return []
    return [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]


def reconstruct_openai_responses(chunks: list[Any]) -> Any:
    """Rebuild a {"output": [...]} dict from the terminal snapshot, else output_item.done, else the text deltas."""
    rebuilt = _reconstruct_output(chunks)
    # A saved prompt resolves its tools server-side; every response snapshot echoes them.
    tools = next(
        (
            getattr(getattr(c, "response", None), "tools", None)
            for c in reversed(chunks)
            if getattr(getattr(c, "response", None), "tools", None)
        ),
        None,
    )
    if tools:
        rebuilt["tools"] = tools
    return rebuilt


def _reconstruct_output(chunks: list[Any]) -> dict[str, Any]:
    snapshot = next(
        (
            getattr(c, "response", None)
            for c in reversed(chunks)
            if getattr(c, "type", "") in ("response.completed", "response.incomplete")
        ),
        None,
    )
    if snapshot is not None:
        return {"output": getattr(snapshot, "output", []) or []}

    done_items = sorted(
        (c for c in chunks if getattr(c, "type", "") == "response.output_item.done"),
        key=lambda c: getattr(c, "output_index", 0),
    )
    if done_items:
        return {"output": [getattr(c, "item", None) for c in done_items if getattr(c, "item", None) is not None]}

    return {"output": _build_output_from_text_deltas(chunks)}
