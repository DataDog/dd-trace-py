import sys
from typing import Any
from typing import Dict
from typing import List
from typing import Optional

import wrapt

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


class BaseTracedGoogleGenAIStreamResponse(wrapt.ObjectProxy):
    def __init__(self, wrapped, integration, span, args, kwargs):
        super().__init__(wrapped)
        self._self_dd_span = span
        self._self_chunks = []
        self._self_args = args
        self._self_kwargs = kwargs
        self._self_dd_integration = integration


class TracedGoogleGenAIStreamResponse(BaseTracedGoogleGenAIStreamResponse):
    def __iter__(self):
        return self

    def __next__(self):
        try:
            chunk = self.__wrapped__.__next__()
            self._self_chunks.append(chunk)
            return chunk
        except StopIteration:
            self._self_dd_integration.llmobs_set_tags(
                self._self_dd_span,
                args=self._self_args,
                kwargs=self._self_kwargs,
                response=_join_chunks(self._self_chunks),
                operation="llm",
            )
            self._self_dd_span.finish()
            raise
        except Exception:
            self._self_dd_span.set_exc_info(*sys.exc_info())
            self._self_dd_span.finish()
            raise


class TracedAsyncGoogleGenAIStreamResponse(BaseTracedGoogleGenAIStreamResponse):
    def __aiter__(self):
        return self

    async def __anext__(self):
        try:
            chunk = await self.__wrapped__.__anext__()
            self._self_chunks.append(chunk)
            return chunk
        except StopAsyncIteration:
            self._self_dd_integration.llmobs_set_tags(
                self._self_dd_span,
                args=self._self_args,
                kwargs=self._self_kwargs,
                response=_join_chunks(self._self_chunks),
                operation="llm",
            )
            self._self_dd_span.finish()
            raise
        except Exception:
            self._self_dd_span.set_exc_info(*sys.exc_info())
            self._self_dd_span.finish()
            raise
