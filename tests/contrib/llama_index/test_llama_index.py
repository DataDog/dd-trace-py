"""APM tests for llama_index integration.

Tests verify that spans are created correctly when wrapping llama_index
base class methods (BaseLLM, BaseQueryEngine, BaseRetriever, BaseEmbedding).
"""

from llama_index.core.base.llms.types import ChatMessage
import pytest

from ddtrace.contrib.internal.llama_index.patch import patch
from ddtrace.contrib.internal.llama_index.patch import unpatch
from ddtrace.ext import SpanKind
from tests.contrib.llama_index.utils import MockEmbedding
from tests.contrib.llama_index.utils import MockErrorLLM
from tests.contrib.llama_index.utils import MockLLM
from tests.contrib.llama_index.utils import MockQueryEngine
from tests.contrib.llama_index.utils import MockRetriever


@pytest.fixture(autouse=True)
def patch_llama_index():
    """Automatically patch llama_index for all tests."""
    patch()
    yield
    unpatch()


class TestLLMChat:
    """Tests for BaseLLM.chat wrapping."""

    def test_chat_success(self, test_spans):
        llm = MockLLM()
        result = llm.chat([ChatMessage(role="user", content="Hello")])

        assert result.message.content == "Mock chat response"

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockLLM.chat"
        assert span.error == 0

    def test_chat_error(self, test_spans):
        llm = MockErrorLLM()

        with pytest.raises(ValueError, match="Mock chat error"):
            llm.chat([ChatMessage(role="user", content="Hello")])

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockErrorLLM.chat"
        assert span.error == 1
        assert span.get_tag("error.type") is not None
        assert span.get_tag("error.message") is not None


class TestLLMComplete:
    """Tests for BaseLLM.complete wrapping."""

    def test_complete_success(self, test_spans):
        llm = MockLLM()
        result = llm.complete("Hello, world!")

        assert result.text == "Mock completion response"

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockLLM.complete"

        assert span.error == 0

    def test_complete_error(self, test_spans):
        llm = MockErrorLLM()

        with pytest.raises(ValueError, match="Mock complete error"):
            llm.complete("Hello, world!")

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockErrorLLM.complete"
        assert span.error == 1
        assert span.get_tag("error.type") is not None
        assert span.get_tag("error.message") is not None


class TestLLMStreamChat:
    """Tests for BaseLLM.stream_chat wrapping."""

    def test_stream_chat_success(self, test_spans):
        llm = MockLLM()
        chunks = list(llm.stream_chat([ChatMessage(role="user", content="Hello")]))

        assert len(chunks) >= 1
        assert chunks[0].message.content == "Mock stream chat response"

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockLLM.stream_chat"

        assert span.error == 0

    def test_stream_chat_error(self, test_spans):
        llm = MockErrorLLM()

        with pytest.raises(ValueError, match="Mock stream_chat error"):
            list(llm.stream_chat([ChatMessage(role="user", content="Hello")]))

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockErrorLLM.stream_chat"
        assert span.error == 1
        assert span.get_tag("error.type") is not None


class TestLLMStreamComplete:
    """Tests for BaseLLM.stream_complete wrapping."""

    def test_stream_complete_success(self, test_spans):
        llm = MockLLM()
        chunks = list(llm.stream_complete("Hello, world!"))

        assert len(chunks) >= 1
        assert chunks[0].text == "Mock stream completion response"

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockLLM.stream_complete"

        assert span.error == 0

    def test_stream_complete_error(self, test_spans):
        llm = MockErrorLLM()

        with pytest.raises(ValueError, match="Mock stream_complete error"):
            list(llm.stream_complete("Hello, world!"))

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockErrorLLM.stream_complete"
        assert span.error == 1
        assert span.get_tag("error.type") is not None


class TestLLMAsync:
    """Tests for async BaseLLM methods."""

    @pytest.mark.asyncio
    async def test_achat_success(self, test_spans):
        llm = MockLLM()
        result = await llm.achat([ChatMessage(role="user", content="Hello")])

        assert result.message.content == "Mock async chat response"

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockLLM.achat"

        assert span.error == 0

    @pytest.mark.asyncio
    async def test_achat_error(self, test_spans):
        llm = MockErrorLLM()

        with pytest.raises(ValueError, match="Mock achat error"):
            await llm.achat([ChatMessage(role="user", content="Hello")])

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockErrorLLM.achat"
        assert span.error == 1

    @pytest.mark.asyncio
    async def test_acomplete_success(self, test_spans):
        llm = MockLLM()
        result = await llm.acomplete("Hello, world!")

        assert result.text == "Mock async completion response"

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockLLM.acomplete"

        assert span.error == 0

    @pytest.mark.asyncio
    async def test_acomplete_error(self, test_spans):
        llm = MockErrorLLM()

        with pytest.raises(ValueError, match="Mock acomplete error"):
            await llm.acomplete("Hello, world!")

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockErrorLLM.acomplete"
        assert span.error == 1


class TestQueryEngine:
    """Tests for BaseQueryEngine wrapping."""

    def test_query_success(self, test_spans):
        qe = MockQueryEngine()
        result = qe.query("What is the meaning of life?")

        assert result.response == "Mock query response"

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockQueryEngine.query"

        assert span.error == 0

    def test_query_error(self, test_spans):
        qe = MockQueryEngine(error=True)

        with pytest.raises(ValueError, match="Mock query error"):
            qe.query("What is the meaning of life?")

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockQueryEngine.query"
        assert span.error == 1
        assert span.get_tag("error.type") is not None
        assert span.get_tag("error.message") is not None

    @pytest.mark.asyncio
    async def test_aquery_success(self, test_spans):
        qe = MockQueryEngine()
        result = await qe.aquery("What is the meaning of life?")

        assert result.response == "Mock async query response"

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockQueryEngine.aquery"

        assert span.error == 0

    @pytest.mark.asyncio
    async def test_aquery_error(self, test_spans):
        qe = MockQueryEngine(error=True)

        with pytest.raises(ValueError, match="Mock aquery error"):
            await qe.aquery("What is the meaning of life?")

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockQueryEngine.aquery"
        assert span.error == 1


class TestRetriever:
    """Tests for BaseRetriever wrapping."""

    def test_retrieve_success(self, test_spans):
        ret = MockRetriever()
        nodes = ret.retrieve("test query")

        assert len(nodes) >= 1
        assert nodes[0].text == "Mock retrieved text"

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockRetriever.retrieve"

        assert span.error == 0

    def test_retrieve_error(self, test_spans):
        ret = MockRetriever(error=True)

        with pytest.raises(ValueError, match="Mock retrieve error"):
            ret.retrieve("test query")

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockRetriever.retrieve"
        assert span.error == 1
        assert span.get_tag("error.type") is not None
        assert span.get_tag("error.message") is not None

    @pytest.mark.asyncio
    async def test_aretrieve_success(self, test_spans):
        ret = MockRetriever()
        nodes = await ret.aretrieve("test query")

        assert len(nodes) >= 1
        assert nodes[0].text == "Mock async retrieved text"

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockRetriever.aretrieve"

        assert span.error == 0

    @pytest.mark.asyncio
    async def test_aretrieve_error(self, test_spans):
        ret = MockRetriever(error=True)

        with pytest.raises(ValueError, match="Mock aretrieve error"):
            await ret.aretrieve("test query")

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockRetriever.aretrieve"
        assert span.error == 1


class TestEmbedding:
    """Tests for BaseEmbedding wrapping."""

    def test_get_query_embedding_success(self, test_spans):
        emb = MockEmbedding()
        result = emb.get_query_embedding("test query")

        assert result == [0.1, 0.2, 0.3]

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockEmbedding.get_query_embedding"

        assert span.error == 0

    def test_get_query_embedding_error(self, test_spans):
        emb = MockEmbedding(error=True)

        with pytest.raises(ValueError, match="Mock get_query_embedding error"):
            emb.get_query_embedding("test query")

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockEmbedding.get_query_embedding"
        assert span.error == 1
        assert span.get_tag("error.type") is not None

    def test_get_text_embedding_batch_success(self, test_spans):
        emb = MockEmbedding()
        result = emb.get_text_embedding_batch(["text one", "text two"])

        assert len(result) == 2
        assert result[0] == [0.4, 0.5, 0.6]

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockEmbedding.get_text_embedding_batch"

        assert span.error == 0

    def test_get_text_embedding_batch_error(self, test_spans):
        emb = MockEmbedding(error=True)

        with pytest.raises(ValueError, match="Mock get_text_embeddings error"):
            emb.get_text_embedding_batch(["text one"])

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockEmbedding.get_text_embedding_batch"
        assert span.error == 1

    @pytest.mark.asyncio
    async def test_aget_query_embedding_success(self, test_spans):
        emb = MockEmbedding()
        result = await emb.aget_query_embedding("test query")

        assert result == [0.1, 0.2, 0.3]

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockEmbedding.aget_query_embedding"

        assert span.error == 0

    @pytest.mark.asyncio
    async def test_aget_query_embedding_error(self, test_spans):
        emb = MockEmbedding(error=True)

        with pytest.raises(ValueError, match="Mock aget_query_embedding error"):
            await emb.aget_query_embedding("test query")

        spans = test_spans.pop_traces()
        assert len(spans) >= 1
        span = spans[0][0]
        assert span.name == "llama_index.request"
        assert span.resource == "MockEmbedding.aget_query_embedding"
        assert span.error == 1


class TestPeerService:
    """Tests for peer service tagging on llama_index spans."""

    def test_span_kind_set_on_llm_span(self, test_spans):
        """Verify span.kind is set to client on LLM spans."""
        llm = MockLLM()
        llm.chat([ChatMessage(role="user", content="Hello")])

        spans = test_spans.pop_traces()
        span = spans[0][0]
        assert span.get_tag("span.kind") == SpanKind.CLIENT

    def test_component_tag_set_on_llm_span(self, test_spans):
        """Verify component tag is set to llama_index on LLM spans."""
        llm = MockLLM()
        llm.chat([ChatMessage(role="user", content="Hello")])

        spans = test_spans.pop_traces()
        span = spans[0][0]
        assert span.get_tag("component") == "llama_index"

    def test_out_host_set_from_api_base(self, test_spans):
        """Verify out.host is extracted from instance api_base URL."""
        llm = MockLLM(api_base="https://api.openai.com/v1")
        llm.chat([ChatMessage(role="user", content="Hello")])

        spans = test_spans.pop_traces()
        span = spans[0][0]
        assert span.get_tag("out.host") == "api.openai.com"

    def test_out_host_not_set_without_api_base(self, test_spans):
        """Verify out.host is not set when api_base is not available."""
        llm = MockLLM()
        llm.chat([ChatMessage(role="user", content="Hello")])

        spans = test_spans.pop_traces()
        span = spans[0][0]
        assert span.get_tag("out.host") is None

    def _enable_peer_service(self):
        """Enable peer service defaults on the tracer's PeerServiceProcessor."""
        from ddtrace.internal.peer_service.processor import PeerServiceProcessor
        from ddtrace.trace import tracer

        for proc in tracer._span_aggregator.dd_processors:
            if isinstance(proc, PeerServiceProcessor):
                proc._set_defaults_enabled = True
                return proc
        return None

    def _restore_peer_service(self, processor, enabled, mapping=None):
        """Restore PeerServiceProcessor to its original state."""
        if processor is not None:
            processor._set_defaults_enabled = enabled
            if mapping is not None:
                processor._mapping = mapping

    def test_peer_service_computed_from_out_host(self, test_spans):
        """Verify peer.service is computed from out.host when peer service defaults are enabled."""
        proc = self._enable_peer_service()
        original = False
        try:
            llm = MockLLM(api_base="https://api.openai.com/v1")
            llm.chat([ChatMessage(role="user", content="Hello")])

            spans = test_spans.pop_traces()
            span = spans[0][0]
            assert span.get_tag("peer.service") == "api.openai.com"
            assert span.get_tag("_dd.peer.service.source") == "out.host"
        finally:
            self._restore_peer_service(proc, original)

    def test_peer_service_remapping(self, test_spans):
        """Verify peer service remapping works."""
        proc = self._enable_peer_service()
        original_mapping = proc._mapping if proc else {}
        original_config_mapping = proc._config._peer_service_mapping if proc else {}
        mapping = {"api.openai.com": "my-openai-service"}
        proc._mapping = mapping
        proc._config._peer_service_mapping = mapping
        try:
            llm = MockLLM(api_base="https://api.openai.com/v1")
            llm.chat([ChatMessage(role="user", content="Hello")])

            spans = test_spans.pop_traces()
            span = spans[0][0]
            assert span.get_tag("peer.service") == "my-openai-service"
            assert span.get_tag("_dd.peer.service.remapped_from") == "api.openai.com"
        finally:
            if proc:
                proc._mapping = original_mapping
                proc._config._peer_service_mapping = original_config_mapping
            self._restore_peer_service(proc, False)

    def test_span_kind_set_on_embedding_span(self, test_spans):
        """Verify span.kind is set to client on embedding spans."""
        emb = MockEmbedding()
        emb.get_query_embedding("test query")

        spans = test_spans.pop_traces()
        span = spans[0][0]
        assert span.get_tag("span.kind") == SpanKind.CLIENT

    def test_span_kind_set_on_query_engine_span(self, test_spans):
        """Verify span.kind is set to client on query engine spans."""
        qe = MockQueryEngine()
        qe.query("test query")

        spans = test_spans.pop_traces()
        span = spans[0][0]
        assert span.get_tag("span.kind") == SpanKind.CLIENT

    def test_span_kind_set_on_retriever_span(self, test_spans):
        """Verify span.kind is set to client on retriever spans."""
        ret = MockRetriever()
        ret.retrieve("test query")

        spans = test_spans.pop_traces()
        span = spans[0][0]
        assert span.get_tag("span.kind") == SpanKind.CLIENT

    @pytest.mark.asyncio
    async def test_span_kind_set_on_async_llm_span(self, test_spans):
        """Verify span.kind is set to client on async LLM spans."""
        llm = MockLLM()
        await llm.achat([ChatMessage(role="user", content="Hello")])

        spans = test_spans.pop_traces()
        span = spans[0][0]
        assert span.get_tag("span.kind") == SpanKind.CLIENT

    def test_peer_service_with_custom_api_base(self, test_spans):
        """Verify peer.service works with custom API base URLs."""
        proc = self._enable_peer_service()
        try:
            llm = MockLLM(api_base="https://my-proxy.internal.company.com/llm/v1")
            llm.chat([ChatMessage(role="user", content="Hello")])

            spans = test_spans.pop_traces()
            span = spans[0][0]
            assert span.get_tag("out.host") == "my-proxy.internal.company.com"
            assert span.get_tag("peer.service") == "my-proxy.internal.company.com"
            assert span.get_tag("_dd.peer.service.source") == "out.host"
        finally:
            self._restore_peer_service(proc, False)
