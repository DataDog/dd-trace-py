"""Reconstruct an Anthropic response dict from buffered RawMessageStreamEvents.

``reconstruct_anthropic(chunks)`` is the AI Guard side analogue of the
contrib's ``_construct_message``: it walks the same event sequence but
produces a plain dict compatible with ``_convert_anthropic_response``.
No SDK types are imported; blocks are plain dicts so ``_get`` (Mapping
protocol) can read them without a hard SDK dependency.
"""

import json
from typing import Any


def reconstruct_anthropic(chunks: list) -> dict[str, Any]:
    """Return a response-shaped dict from buffered RawMessageStreamEvents.

    Output format matches what ``_convert_anthropic_response`` expects::

        {"content": [{"type": "text", "text": "..."}, ...]}

    Content block types handled: ``text``, ``thinking``, ``tool_use``,
    ``server_tool_use``. Unknown types are passed through as
    ``{"type": block_type}`` so AI Guard's ``_format_content_blocks``
    can decide whether to drop them.
    """
    content = []
    current_block = None

    for chunk in chunks:
        chunk_type = getattr(chunk, "type", "") or ""

        if chunk_type == "content_block_start":
            block = getattr(chunk, "content_block", None)
            if block is None:
                continue
            block_type = getattr(block, "type", "") or ""
            if block_type == "text":
                current_block = {"type": "text", "text": getattr(block, "text", "") or ""}
            elif block_type == "thinking":
                current_block = {"type": "thinking", "thinking": getattr(block, "thinking", "") or ""}
            elif block_type in ("tool_use", "server_tool_use"):
                current_block = {
                    "type": block_type,
                    "id": getattr(block, "id", "") or "",
                    "name": getattr(block, "name", "") or "",
                    # accumulated as partial JSON strings; parsed to dict at content_block_stop
                    "input": "",
                }
            else:
                current_block = {"type": block_type}
            content.append(current_block)

        elif chunk_type == "content_block_delta":
            if current_block is None:
                continue
            delta = getattr(chunk, "delta", None)
            if delta is None:
                continue
            delta_type = getattr(delta, "type", "") or ""
            if delta_type == "text_delta":
                current_block["text"] = current_block.get("text", "") + (getattr(delta, "text", "") or "")
            elif delta_type == "thinking_delta":
                current_block["thinking"] = current_block.get("thinking", "") + (getattr(delta, "thinking", "") or "")
            elif delta_type == "input_json_delta":
                current_block["input"] = current_block.get("input", "") + (getattr(delta, "partial_json", "") or "")

        elif chunk_type == "content_block_stop":
            if current_block is not None:
                block_type = current_block.get("type", "")
                if block_type in ("tool_use", "server_tool_use"):
                    # Parse the accumulated JSON string into a dict so
                    # _tool_call_from_block can call json.dumps(input, ...) on it.
                    input_str = current_block.get("input", "")
                    try:
                        current_block["input"] = json.loads(input_str) if input_str else {}
                    except (ValueError, TypeError):
                        current_block["input"] = {}
            current_block = None

    return {"content": content}
