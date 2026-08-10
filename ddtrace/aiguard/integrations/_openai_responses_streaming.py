from typing import Any


def _build_output_from_text_deltas(chunks: list[Any]) -> list[Any]:
    """Last-resort reconstruction: concatenate ``response.output_text.delta`` events into one
    assistant ``message`` item. Used only when no snapshot or ``output_item.done`` events exist.
    """
    text = "".join(
        str(getattr(c, "delta", ""))
        for c in chunks
        if getattr(c, "type", "") == "response.output_text.delta" and getattr(c, "delta", None)
    )
    if not text:
        return []
    return [{"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": text}]}]


def reconstruct_openai_responses(chunks: list[Any]) -> Any:
    """Rebuild a ``{"output": [...]}`` dict from buffered events, preferring the terminal
    ``response.completed``/``incomplete`` snapshot, then ``output_item.done``, then text deltas.
    """
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
