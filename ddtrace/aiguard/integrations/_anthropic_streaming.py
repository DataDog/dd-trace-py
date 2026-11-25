from typing import Any

from ddtrace.aiguard._common import _get
from ddtrace.contrib.internal.anthropic._streaming import _construct_message


def reconstruct_anthropic(chunks: list[Any]) -> Any:
    """Rebuild a response-shaped dict from buffered ``RawMessageStreamEvent`` chunks."""
    message = _construct_message(chunks)  # type: ignore[no-untyped-call]
    # The contrib adds one block per content_block_start, but drops the server_name of mcp_tool_use
    # and renames mcp_tool_result to tool_result: restore both from those chunks.
    starts = (
        _get(chunk, "content_block")
        for chunk in chunks
        if _get(chunk, "type") == "content_block_start" and _get(chunk, "content_block")
    )
    for block, start in zip(message["content"], starts):
        start_type = _get(start, "type")
        if start_type == "mcp_tool_use":
            block["server_name"] = _get(start, "server_name")
            # The contrib only reads streamed input deltas: keep an input sent whole in the start chunk.
            if not block.get("input") and _get(start, "input"):
                block["input"] = _get(start, "input")
        elif start_type == "mcp_tool_result":
            block["type"] = start_type
    return message
