from typing import Any

from ddtrace.contrib.internal.anthropic._streaming import _construct_message


def reconstruct_anthropic(chunks: list[Any]) -> Any:
    """Rebuild a response-shaped dict from buffered ``RawMessageStreamEvent`` chunks."""
    return _construct_message(chunks)  # type: ignore[no-untyped-call]
