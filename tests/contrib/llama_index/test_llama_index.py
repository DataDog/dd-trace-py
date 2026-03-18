import pytest

from tests.utils import override_global_config


# ---------------------------------------------------------------------------
# Mock subclasses for abstract base classes
#
# LlamaIndex query engines, retrievers, and embeddings are abstract —
# they cannot be instantiated directly.  These minimal concrete subclasses
# implement the required abstract methods so we can exercise the public
# API (query, retrieve, get_query_embedding, …) without making any real
# HTTP calls or needing VCR cassettes.
# ---------------------------------------------------------------------------


def _make_mock_query_engine():
    """Return a fresh MockQueryEngine instance.

    Must be called *after* llama_index has been patched so the imports
    resolve against an already-instrumented module.
    """
    from llama_index.core.base.base_query_engine import BaseQueryEngine
    from llama_index.core.base.response.schema import Response

    response_obj = Response(response="The answer is 42.")

    class MockQueryEngine(BaseQueryEngine):
        def _query(self, query_bundle):
            return response_obj

        async def _aquery(self, query_bundle):
            return response_obj

        def _get_prompt_modules(self):
            return {}

    return MockQueryEngine(callback_manager=None)


def _make_mock_retriever():
    """Return a fresh MockRetriever instance.

    Must be called *after* llama_index has been patched.
    """
    from llama_index.core.base.base_retriever import BaseRetriever
    from llama_index.core.schema import NodeWithScore
    from llama_index.core.schema import TextNode

    mock_nodes = [NodeWithScore(node=TextNode(text="Document text"), score=0.95)]

    class MockRetriever(BaseRetriever):
        def _retrieve(self, query_bundle):
            return mock_nodes

        async def _aretrieve(self, query_bundle):
            return mock_nodes

    return MockRetriever(callback_manager=None)


def _make_mock_embedding():
    """Return a fresh MockEmbedding instance.

    Must be called *after* llama_index has been patched.
    """
    from llama_index.core.base.embeddings.base import BaseEmbedding

    class MockEmbedding(BaseEmbedding):
        def _get_query_embedding(self, query):
            return [0.1, 0.2, 0.3]

        def _get_text_embedding(self, text):
            return [0.1, 0.2, 0.3]

        async def _aget_query_embedding(self, query):
            return [0.1, 0.2, 0.3]

        def _get_text_embeddings(self, texts):
            return [[0.1, 0.2, 0.3] for _ in texts]

    return MockEmbedding(model_name="mock-embed")


# ---------------------------------------------------------------------------
# Non-snapshot test — uses test_spans for manual tag assertions
# ---------------------------------------------------------------------------


def test_global_tags(llama_index, request_vcr, test_spans):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        cassette_name = "llama_index_completion.yaml"
        with request_vcr.use_cassette(cassette_name):
            response = llm.chat(messages=[ChatMessage(role="user", content="Hello")])

    assert response.message.content, "Expected non-empty response content from instrumented call"
    span = test_spans.pop_traces()[0][0]
    assert span.resource == "OpenAI.chat"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("llama_index.request.model") == "gpt-4o-mini"
    assert span.error == 0


# ---------------------------------------------------------------------------
# Snapshot tests — LLM calls with VCR cassettes
# ---------------------------------------------------------------------------


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_chat")
def test_llama_index_chat(llama_index, request_vcr):
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_completion.yaml"):
        response = llm.chat(messages=[ChatMessage(role="user", content="Hello")])

    assert response.message.content, "Expected non-empty response content"


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_complete")
def test_llama_index_complete(llama_index, request_vcr):
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_complete.yaml"):
        response = llm.complete("What is the meaning of life?")

    assert response.text, "Expected non-empty response text"


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_chat_stream")
def test_llama_index_chat_stream(llama_index, request_vcr):
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_chat_stream.yaml"):
        response = llm.stream_chat(messages=[ChatMessage(role="user", content="Hello")])
        for _ in response:
            pass


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_complete_stream")
def test_llama_index_complete_stream(llama_index, request_vcr):
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_complete_stream.yaml"):
        response = llm.stream_complete("What is the meaning of life?")
        for _ in response:
            pass


@pytest.mark.snapshot(
    token="tests.contrib.llama_index.test_llama_index.test_llama_index_chat_error",
    ignores=["meta.error.stack"],
)
def test_llama_index_chat_error(llama_index, request_vcr):
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with pytest.raises(Exception):
        with request_vcr.use_cassette("llama_index_chat_error.yaml"):
            llm.chat(messages=[ChatMessage(role="user", content="Hello")])


async def test_llama_index_chat_async(llama_index, request_vcr, snapshot_context):
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

    with snapshot_context(token="tests.contrib.llama_index.test_llama_index.test_llama_index_chat_async"):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
        with request_vcr.use_cassette("llama_index_completion.yaml"):
            response = await llm.achat(messages=[ChatMessage(role="user", content="Hello")])

        assert response.message.content, "Expected non-empty response content"


async def test_llama_index_complete_async(llama_index, request_vcr, snapshot_context):
    from llama_index.llms.openai import OpenAI

    with snapshot_context(token="tests.contrib.llama_index.test_llama_index.test_llama_index_complete_async"):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
        with request_vcr.use_cassette("llama_index_complete.yaml"):
            response = await llm.acomplete("What is the meaning of life?")

        assert response.text, "Expected non-empty response text"


# ---------------------------------------------------------------------------
# Snapshot tests — non-LLM operations with mock subclasses
# ---------------------------------------------------------------------------


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_query_engine")
def test_llama_index_query_engine(llama_index):
    """Test that BaseQueryEngine.query() is traced."""
    engine = _make_mock_query_engine()
    engine.query("What is the meaning of life?")


async def test_llama_index_query_engine_async(llama_index, snapshot_context):
    """Test that BaseQueryEngine.aquery() is traced."""
    with snapshot_context(token="tests.contrib.llama_index.test_llama_index.test_llama_index_query_engine_async"):
        engine = _make_mock_query_engine()
        await engine.aquery("What is the meaning of life?")


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_retriever")
def test_llama_index_retriever(llama_index):
    """Test that BaseRetriever.retrieve() is traced."""
    retriever = _make_mock_retriever()
    retriever.retrieve("test query")


async def test_llama_index_retriever_async(llama_index, snapshot_context):
    """Test that BaseRetriever.aretrieve() is traced."""
    with snapshot_context(token="tests.contrib.llama_index.test_llama_index.test_llama_index_retriever_async"):
        retriever = _make_mock_retriever()
        await retriever.aretrieve("test query")


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_embedding_query")
def test_llama_index_embedding_query(llama_index):
    """Test that BaseEmbedding.get_query_embedding() is traced with mock."""
    embed = _make_mock_embedding()
    embed.get_query_embedding("test query")


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_embedding_batch")
def test_llama_index_embedding_batch(llama_index):
    """Test that BaseEmbedding.get_text_embedding_batch() is traced with mock."""
    embed = _make_mock_embedding()
    embed.get_text_embedding_batch(["doc one", "doc two"])


# ---------------------------------------------------------------------------
# Snapshot tests — real OpenAI embedding model with VCR cassettes
# ---------------------------------------------------------------------------


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_openai_embedding")
def test_llama_index_openai_embedding(llama_index, request_vcr):
    """Test that OpenAIEmbedding.get_query_embedding() is traced with real model name."""
    from llama_index.embeddings.openai import OpenAIEmbedding

    embed = OpenAIEmbedding(model_name="text-embedding-ada-002")
    with request_vcr.use_cassette("llama_index_openai_embedding.yaml"):
        result = embed.get_query_embedding("test query")

    assert result, "Expected non-empty embedding result"


# ---------------------------------------------------------------------------
# Snapshot tests — predict (LLM, uses VCR cassettes)
# ---------------------------------------------------------------------------


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_predict")
def test_llama_index_predict(llama_index, request_vcr):
    """Test that LLM.predict() is traced."""
    from llama_index.core import PromptTemplate
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_complete.yaml"):
        response = llm.predict(PromptTemplate("What is the meaning of life?"))

    assert response, "Expected non-empty response"


async def test_llama_index_apredict(llama_index, request_vcr, snapshot_context):
    """Test that LLM.apredict() is traced."""
    from llama_index.core import PromptTemplate
    from llama_index.llms.openai import OpenAI

    with snapshot_context(token="tests.contrib.llama_index.test_llama_index.test_llama_index_apredict"):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
        with request_vcr.use_cassette("llama_index_complete.yaml"):
            response = await llm.apredict(PromptTemplate("What is the meaning of life?"))

        assert response, "Expected non-empty response"
