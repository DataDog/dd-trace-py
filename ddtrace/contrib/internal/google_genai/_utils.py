from typing import Any
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.llmobs._integrations.base_stream_handler import AsyncStreamHandler
from ddtrace.llmobs._integrations.base_stream_handler import StreamHandler
from ddtrace.llmobs._integrations.google_utils import GOOGLE_GENAI_DEFAULT_MODEL_ROLE
from ddtrace.llmobs._utils import _get_attr


def _join_chunks(chunks: List[Any]) -> Optional[Dict[str, Any]]:
    """
    Consolidates streamed response GenerateContentResponse chunks into a single dictionary representing the response.
    All chunks should have the same role since one generation call produces consistent content type.
    """
    if not chunks:
        return None

    text_chunks = []
    non_text_parts = []
    role = None

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
                        text_chunks.append(text)
                    else:
                        non_text_parts.append(part)

        parts = []
        if text_chunks:
            parts.append({"text": "".join(text_chunks)})
        parts.extend(non_text_parts)

        merged_response = {"candidates": [{"content": {"role": role, "parts": parts}}] if parts else []}

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
