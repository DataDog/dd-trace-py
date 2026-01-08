from collections import defaultdict
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.google_utils import GOOGLE_GENAI_DEFAULT_MODEL_ROLE
from ddtrace.llmobs._utils import _get_attr


REASONING_ROLE = "reasoning"


def _join_chunks(chunks: List[Any]) -> Optional[Dict[str, Any]]:
    """
    Consolidates streamed response GenerateContentResponse chunks into a single dictionary representing the response.
    """
    if not chunks:
        return None

    non_text_parts = []
    role = None

    parts_by_role: dict[str, list[str]] = defaultdict(list)

    try:
        for chunk in chunks:
            candidates = _get_attr(chunk, "candidates", [])
            for candidate in candidates:
                content = _get_attr(candidate, "content", None)
                if not content:
                    continue

                if role is None:
                    role = _get_attr(content, "role", GOOGLE_GENAI_DEFAULT_MODEL_ROLE)

                parts = _get_attr(content, "parts", [])
                for part in parts:
                    text = _get_attr(part, "text", None)
                    if text:
                        if getattr(part, "thought", False):
                            parts_by_role[REASONING_ROLE].append(text)
                        else:
                            parts_by_role[role].append(text)
                    else:
                        non_text_parts.append(part)

        all_parts = []
        for role, parts in parts_by_role.items():
            part: dict = {"text": "".join(parts)}
            if role == REASONING_ROLE:
                part["thought"] = True
            all_parts.append(part)
        all_parts.extend(non_text_parts)

        merged_response = {"candidates": [{"content": {"role": role, "parts": all_parts}}] if all_parts else []}

        last_chunk = chunks[-1]
        merged_response["usage_metadata"] = _get_attr(last_chunk, "usage_metadata", {})

        return merged_response
    except Exception:
        # if error processing chunks, return None to avoid crashing the user app
        return None


class BaseGoogleGenAIStreamHandler:
    def finalize_stream(self, exception=None):
        self.integration.llmobs_set_tags(
            self.primary_span,
            args=self.request_args,
            kwargs=self.request_kwargs,
            response=_join_chunks(self.chunks),
            operation="llm",
        )
        self.primary_span.finish()


class GoogleGenAIStreamHandler(BaseGoogleGenAIStreamHandler, StreamHandler):
    def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)


class GoogleGenAIAsyncStreamHandler(BaseGoogleGenAIStreamHandler, AsyncStreamHandler):
    async def process_chunk(self, chunk, iterator=None):
        self.chunks.append(chunk)
