import collections
from typing import Any

from ddtrace.llmobs._integrations.utils import openai_construct_message_from_streamed_chunks


def reconstruct_openai_chat(chunks: list[Any]) -> Any:
    """Rebuild a ChatCompletion-shaped dict from buffered ``ChatCompletionChunk`` objects.

    Each chunk carries a ``choices`` list of per-index deltas; we group those by
    choice index and let the LLMObs helper aggregate role/content/tool_calls.
    Usage-only trailing chunks carry no ``choices`` and are naturally ignored.
    The resulting ``{"choices": [{"message": {...}}]}`` shape matches what
    ``_convert_openai_response`` reads via ``_get``.
    """
    by_choice: "collections.OrderedDict[int, list[Any]]" = collections.OrderedDict()
    for chunk in chunks:
        for choice in getattr(chunk, "choices", None) or []:
            by_choice.setdefault(getattr(choice, "index", 0), []).append(choice)
    return {
        "choices": [
            {"message": openai_construct_message_from_streamed_chunks(choices)} for choices in by_choice.values()
        ]
    }
