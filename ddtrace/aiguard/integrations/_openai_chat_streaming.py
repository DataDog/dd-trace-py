import collections
from typing import Any

from ddtrace.llmobs._integrations.utils import openai_construct_message_from_streamed_chunks


def reconstruct_openai_chat(chunks: list[Any]) -> Any:
    """Rebuild a ``{"choices": [{"message": {...}}]}`` dict from buffered ChatCompletionChunks.

    Groups per-index deltas by choice; the LLMObs helper aggregates role/content/tool_calls.
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
