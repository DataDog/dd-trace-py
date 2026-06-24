from typing import Any


def _build_output_from_text_deltas(chunks: list[Any]) -> list[Any]:
    """Last-resort reconstruction: stitch ``response.output_text.delta`` events.

    Used only when no terminal snapshot (``response.completed`` /
    ``response.incomplete``) and no ``response.output_item.done`` events were
    seen. Produces a single assistant ``message`` item whose ``output_text``
    part is the concatenation of every text delta, which
    ``_convert_openai_response_output`` reads via ``_extract_text_content``.
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
    """Rebuild a Responses-shaped ``{"output": [...]}`` dict from buffered events.

    Preference order (per the SDK's event semantics):
      1. The terminal ``response.completed`` / ``response.incomplete`` snapshot,
         which carries the full generated ``output`` (a normal incomplete stream,
         e.g. ``max_output_tokens``, still carries the generated text).
      2. ``response.output_item.done`` events, ordered by ``output_index``.
      3. Concatenated ``response.output_text.delta`` events.

    ``_convert_openai_response_output`` reads ``output`` via ``_get`` so the
    dict shape works directly.
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
