"""Test utilities for llama_index integration tests.

Provides mock implementations of llama_index base classes for testing
without requiring real API keys or external services.
"""

from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Sequence

from llama_index.core.base.base_query_engine import BaseQueryEngine
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.base.llms.base import BaseLLM
from llama_index.core.base.llms.types import ChatMessage
from llama_index.core.base.llms.types import ChatResponse
from llama_index.core.base.llms.types import CompletionResponse
from llama_index.core.base.llms.types import LLMMetadata
from llama_index.core.base.response.schema import Response
from llama_index.core.schema import NodeWithScore
from llama_index.core.schema import QueryBundle
from llama_index.core.schema import TextNode


class MockLLM(BaseLLM):
    """Concrete mock LLM for testing. Implements all abstract methods."""

    model: str = "mock-model"
    api_base: Optional[str] = None

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(model_name=self.model)

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(role="assistant", content="Mock chat response"),
        )

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        return CompletionResponse(text="Mock completion response")

    def stream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any):
        yield ChatResponse(
            message=ChatMessage(role="assistant", content="Mock stream chat response"),
        )

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        yield CompletionResponse(text="Mock stream completion response")

    async def achat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(role="assistant", content="Mock async chat response"),
        )

    async def acomplete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        return CompletionResponse(text="Mock async completion response")

    async def astream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any):
        yield ChatResponse(
            message=ChatMessage(role="assistant", content="Mock async stream chat response"),
        )

    async def astream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        yield CompletionResponse(text="Mock async stream completion response")


class MockLLMWithUsage(BaseLLM):
    """Mock LLM that includes token usage in responses via raw field."""

    model: str = "mock-model-with-usage"
    api_base: Optional[str] = None

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(model_name=self.model)

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(role="assistant", content="Mock chat response with usage"),
            raw={"usage": {"prompt_tokens": 10, "completion_tokens": 20}},
        )

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        return CompletionResponse(
            text="Mock completion response with usage",
            raw={"usage": {"prompt_tokens": 5, "completion_tokens": 15}},
        )

    def stream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any):
        yield ChatResponse(
            message=ChatMessage(role="assistant", content="Mock stream chat response"),
        )

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        yield CompletionResponse(text="Mock stream completion response")

    async def achat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        return ChatResponse(
            message=ChatMessage(role="assistant", content="Mock async chat response with usage"),
            raw={"usage": {"prompt_tokens": 10, "completion_tokens": 20}},
        )

    async def acomplete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        return CompletionResponse(
            text="Mock async completion response with usage",
            raw={"usage": {"prompt_tokens": 5, "completion_tokens": 15}},
        )

    async def astream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any):
        yield ChatResponse(
            message=ChatMessage(role="assistant", content="Mock async stream chat response"),
        )

    async def astream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        yield CompletionResponse(text="Mock async stream completion response")


class MockErrorLLM(BaseLLM):
    """Mock LLM that raises errors for error-path testing."""

    model: str = "mock-error-model"

    @property
    def metadata(self) -> LLMMetadata:
        return LLMMetadata(model_name=self.model)

    def chat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        raise ValueError("Mock chat error")

    def complete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        raise ValueError("Mock complete error")

    def stream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any):
        raise ValueError("Mock stream_chat error")

    def stream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        raise ValueError("Mock stream_complete error")

    async def achat(self, messages: Sequence[ChatMessage], **kwargs: Any) -> ChatResponse:
        raise ValueError("Mock achat error")

    async def acomplete(self, prompt: str, formatted: bool = False, **kwargs: Any) -> CompletionResponse:
        raise ValueError("Mock acomplete error")

    async def astream_chat(self, messages: Sequence[ChatMessage], **kwargs: Any):
        raise ValueError("Mock astream_chat error")

    async def astream_complete(self, prompt: str, formatted: bool = False, **kwargs: Any):
        raise ValueError("Mock astream_complete error")


class MockQueryEngine(BaseQueryEngine):
    """Concrete mock query engine for testing."""

    def __init__(self, error: bool = False):
        self._error = error
        super().__init__(callback_manager=None)

    def _query(self, query_bundle: QueryBundle):
        if self._error:
            raise ValueError("Mock query error")
        return Response(response="Mock query response")

    async def _aquery(self, query_bundle: QueryBundle):
        if self._error:
            raise ValueError("Mock aquery error")
        return Response(response="Mock async query response")

    def _get_prompt_modules(self) -> Dict[str, Any]:
        return {}


class MockRetriever(BaseRetriever):
    """Concrete mock retriever for testing."""

    def __init__(self, error: bool = False):
        self._error = error
        super().__init__(callback_manager=None)

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        if self._error:
            raise ValueError("Mock retrieve error")
        return [NodeWithScore(node=TextNode(text="Mock retrieved text"), score=0.95)]

    async def _aretrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        if self._error:
            raise ValueError("Mock aretrieve error")
        return [NodeWithScore(node=TextNode(text="Mock async retrieved text"), score=0.90)]


class MockEmbedding(BaseEmbedding):
    """Concrete mock embedding model for testing."""

    should_error: bool = False

    def __init__(self, error: bool = False, **kwargs: Any):
        super().__init__(model_name="mock-embedding-model", should_error=error, **kwargs)

    def _get_query_embedding(self, query: str) -> List[float]:
        if self.should_error:
            raise ValueError("Mock get_query_embedding error")
        return [0.1, 0.2, 0.3]

    async def _aget_query_embedding(self, query: str) -> List[float]:
        if self.should_error:
            raise ValueError("Mock aget_query_embedding error")
        return [0.1, 0.2, 0.3]

    def _get_text_embedding(self, text: str) -> List[float]:
        if self.should_error:
            raise ValueError("Mock get_text_embedding error")
        return [0.4, 0.5, 0.6]

    def _get_text_embeddings(self, texts: List[str]) -> List[List[float]]:
        if self.should_error:
            raise ValueError("Mock get_text_embeddings error")
        return [[0.4, 0.5, 0.6] for _ in texts]
