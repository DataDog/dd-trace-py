from llama_index.core import PromptTemplate
from llama_index.core.base.base_query_engine import BaseQueryEngine
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.base.embeddings.base import BaseEmbedding
from llama_index.core.base.response.schema import Response
from llama_index.core.llms import ChatMessage
from llama_index.core.schema import NodeWithScore
from llama_index.core.schema import TextNode
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI
import pytest

from tests.utils import override_global_config


def _make_mock_query_engine():
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
    mock_nodes = [NodeWithScore(node=TextNode(text="Document text"), score=0.95)]

    class MockRetriever(BaseRetriever):
        def _retrieve(self, query_bundle):
            return mock_nodes

        async def _aretrieve(self, query_bundle):
            return mock_nodes

    return MockRetriever(callback_manager=None)


def _make_mock_embedding():
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


def test_global_tags(llama_index, request_vcr, test_spans):
    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        with request_vcr.use_cassette("llama_index_completion.yaml"):
            llm.chat(messages=[ChatMessage(role="user", content="Hello")])

    span = test_spans.pop_traces()[0][0]
    assert span.resource == "gpt-4o-mini"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("llama_index.request.model") == "gpt-4o-mini"
    assert span.error == 0


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_chat")
def test_llama_index_chat(llama_index, request_vcr):
    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_completion.yaml"):
        llm.chat(messages=[ChatMessage(role="user", content="Hello")])


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_complete")
def test_llama_index_complete(llama_index, request_vcr):
    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_complete.yaml"):
        llm.complete("What is the meaning of life?")


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_chat_stream")
def test_llama_index_chat_stream(llama_index, request_vcr):
    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_chat_stream.yaml"):
        response = llm.stream_chat(messages=[ChatMessage(role="user", content="Hello")])
        for _ in response:
            pass


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_complete_stream")
def test_llama_index_complete_stream(llama_index, request_vcr):
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
    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with pytest.raises(Exception):
        with request_vcr.use_cassette("llama_index_chat_error.yaml"):
            llm.chat(messages=[ChatMessage(role="user", content="Hello")])


async def test_llama_index_chat_async(llama_index, request_vcr, snapshot_context):
    with snapshot_context(token="tests.contrib.llama_index.test_llama_index.test_llama_index_chat_async"):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
        with request_vcr.use_cassette("llama_index_completion.yaml"):
            await llm.achat(messages=[ChatMessage(role="user", content="Hello")])


async def test_llama_index_complete_async(llama_index, request_vcr, snapshot_context):
    with snapshot_context(token="tests.contrib.llama_index.test_llama_index.test_llama_index_complete_async"):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
        with request_vcr.use_cassette("llama_index_complete.yaml"):
            await llm.acomplete("What is the meaning of life?")


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_query_engine")
def test_llama_index_query_engine(llama_index):
    engine = _make_mock_query_engine()
    engine.query("What is the meaning of life?")


async def test_llama_index_query_engine_async(llama_index, snapshot_context):
    with snapshot_context(token="tests.contrib.llama_index.test_llama_index.test_llama_index_query_engine_async"):
        engine = _make_mock_query_engine()
        await engine.aquery("What is the meaning of life?")


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_retriever")
def test_llama_index_retriever(llama_index):
    retriever = _make_mock_retriever()
    retriever.retrieve("test query")


async def test_llama_index_retriever_async(llama_index, snapshot_context):
    with snapshot_context(token="tests.contrib.llama_index.test_llama_index.test_llama_index_retriever_async"):
        retriever = _make_mock_retriever()
        await retriever.aretrieve("test query")


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_embedding_query")
def test_llama_index_embedding_query(llama_index):
    embed = _make_mock_embedding()
    embed.get_query_embedding("test query")


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_embedding_batch")
def test_llama_index_embedding_batch(llama_index):
    embed = _make_mock_embedding()
    embed.get_text_embedding_batch(["doc one", "doc two"])


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_openai_embedding")
def test_llama_index_openai_embedding(llama_index, request_vcr):
    embed = OpenAIEmbedding(model_name="text-embedding-ada-002")
    with request_vcr.use_cassette("llama_index_openai_embedding.yaml"):
        embed.get_query_embedding("test query")


@pytest.mark.snapshot(token="tests.contrib.llama_index.test_llama_index.test_llama_index_predict")
def test_llama_index_predict(llama_index, request_vcr):
    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_complete.yaml"):
        llm.predict(PromptTemplate("What is the meaning of life?"))


async def test_llama_index_apredict(llama_index, request_vcr, snapshot_context):
    with snapshot_context(token="tests.contrib.llama_index.test_llama_index.test_llama_index_apredict"):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
        with request_vcr.use_cassette("llama_index_complete.yaml"):
            await llm.apredict(PromptTemplate("What is the meaning of life?"))
