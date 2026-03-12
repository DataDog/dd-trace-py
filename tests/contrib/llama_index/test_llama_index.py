import mock
import pytest

from tests.utils import override_global_config


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
    assert span.get_tag("component") == "llama_index"
    assert span.error == 0


def test_llama_index_chat(llama_index, request_vcr, test_spans):
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_completion.yaml"):
        response = llm.chat(messages=[ChatMessage(role="user", content="Hello")])

    assert response.message.content, "Expected non-empty response content"
    span = test_spans.pop_traces()[0][0]
    assert span.resource == "OpenAI.chat"
    assert span.get_tag("llama_index.request.model") == "gpt-4o-mini"
    assert span.get_tag("component") == "llama_index"
    assert span.error == 0


def test_llama_index_complete(llama_index, request_vcr, test_spans):
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_complete.yaml"):
        response = llm.complete("What is the meaning of life?")

    assert response.text, "Expected non-empty response text"
    span = test_spans.pop_traces()[0][0]
    assert span.resource == "OpenAI.complete"
    assert span.get_tag("llama_index.request.model") == "gpt-4o-mini"
    assert span.get_tag("component") == "llama_index"
    assert span.error == 0


def test_llama_index_chat_stream(llama_index, request_vcr, test_spans):
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_chat_stream.yaml"):
        response = llm.stream_chat(messages=[ChatMessage(role="user", content="Hello")])
        for _ in response:
            pass

    span = test_spans.pop_traces()[0][0]
    assert span.resource == "OpenAI.stream_chat"
    assert span.get_tag("component") == "llama_index"
    assert span.error == 0


def test_llama_index_complete_stream(llama_index, request_vcr, test_spans):
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_complete_stream.yaml"):
        response = llm.stream_complete("What is the meaning of life?")
        for _ in response:
            pass

    span = test_spans.pop_traces()[0][0]
    assert span.resource == "OpenAI.stream_complete"
    assert span.get_tag("component") == "llama_index"
    assert span.error == 0


def test_llama_index_chat_error(llama_index, request_vcr, test_spans):
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with pytest.raises(Exception):
        with request_vcr.use_cassette("llama_index_chat_error.yaml"):
            llm.chat(messages=[ChatMessage(role="user", content="Hello")])

    span = test_spans.pop_traces()[0][0]
    assert span.resource == "OpenAI.chat"
    assert span.error == 1


async def test_llama_index_chat_async(llama_index, request_vcr, test_spans):
    from llama_index.core.llms import ChatMessage
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_completion.yaml"):
        response = await llm.achat(messages=[ChatMessage(role="user", content="Hello")])

    assert response.message.content, "Expected non-empty response content"
    span = test_spans.pop_traces()[0][0]
    assert span.resource == "OpenAI.achat"
    assert span.get_tag("llama_index.request.model") == "gpt-4o-mini"
    assert span.get_tag("component") == "llama_index"
    assert span.error == 0


async def test_llama_index_complete_async(llama_index, request_vcr, test_spans):
    from llama_index.llms.openai import OpenAI

    llm = OpenAI(model="gpt-4o-mini", max_tokens=15)
    with request_vcr.use_cassette("llama_index_complete.yaml"):
        response = await llm.acomplete("What is the meaning of life?")

    assert response.text, "Expected non-empty response text"
    span = test_spans.pop_traces()[0][0]
    assert span.resource == "OpenAI.acomplete"
    assert span.get_tag("llama_index.request.model") == "gpt-4o-mini"
    assert span.get_tag("component") == "llama_index"
    assert span.error == 0


def test_llama_index_query_engine(llama_index, test_spans):
    """Test that BaseQueryEngine.query() is traced with correct resource name."""
    from llama_index.core.base.base_query_engine import BaseQueryEngine

    # Create a mock response with a .response attribute (like a real Response object)
    mock_response = mock.MagicMock()
    mock_response.response = "The answer is 42."

    # Create a concrete subclass with mocked _query
    class MockQueryEngine(BaseQueryEngine):
        def _query(self, query_bundle):
            return mock_response

        async def _aquery(self, query_bundle):
            return mock_response

    engine = MockQueryEngine(callback_manager=None)
    engine.query("What is the meaning of life?")

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    # Find the query engine span (there may be nested LLM spans too)
    query_span = None
    for trace in traces:
        for span in trace:
            if span.resource == "MockQueryEngine.query":
                query_span = span
                break
    assert query_span is not None, "Expected a span with resource 'MockQueryEngine.query'"


async def test_llama_index_query_engine_async(llama_index, test_spans):
    """Test that BaseQueryEngine.aquery() is traced with correct resource name."""
    from llama_index.core.base.base_query_engine import BaseQueryEngine

    mock_response = mock.MagicMock()
    mock_response.response = "The answer is 42."

    class MockQueryEngine(BaseQueryEngine):
        def _query(self, query_bundle):
            return mock_response

        async def _aquery(self, query_bundle):
            return mock_response

    engine = MockQueryEngine(callback_manager=None)
    await engine.aquery("What is the meaning of life?")

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    query_span = None
    for trace in traces:
        for span in trace:
            if span.resource == "MockQueryEngine.aquery":
                query_span = span
                break
    assert query_span is not None, "Expected a span with resource 'MockQueryEngine.aquery'"


def test_llama_index_retriever(llama_index, test_spans):
    """Test that BaseRetriever.retrieve() is traced with correct resource name."""
    from llama_index.core.base.base_retriever import BaseRetriever
    from llama_index.core.schema import NodeWithScore, TextNode

    mock_nodes = [NodeWithScore(node=TextNode(text="Document text"), score=0.95)]

    class MockRetriever(BaseRetriever):
        def _retrieve(self, query_bundle):
            return mock_nodes

    retriever = MockRetriever(callback_manager=None)
    retriever.retrieve("test query")

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    retriever_span = None
    for trace in traces:
        for span in trace:
            if span.resource == "MockRetriever.retrieve":
                retriever_span = span
                break
    assert retriever_span is not None, "Expected a span with resource 'MockRetriever.retrieve'"


async def test_llama_index_retriever_async(llama_index, test_spans):
    """Test that BaseRetriever.aretrieve() is traced with correct resource name."""
    from llama_index.core.base.base_retriever import BaseRetriever
    from llama_index.core.schema import NodeWithScore, TextNode

    mock_nodes = [NodeWithScore(node=TextNode(text="Document text"), score=0.95)]

    class MockRetriever(BaseRetriever):
        def _retrieve(self, query_bundle):
            return mock_nodes

        async def _aretrieve(self, query_bundle):
            return mock_nodes

    retriever = MockRetriever(callback_manager=None)
    await retriever.aretrieve("test query")

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    retriever_span = None
    for trace in traces:
        for span in trace:
            if span.resource == "MockRetriever.aretrieve":
                retriever_span = span
                break
    assert retriever_span is not None, "Expected a span with resource 'MockRetriever.aretrieve'"


def test_llama_index_embedding_query(llama_index, test_spans):
    """Test that BaseEmbedding.get_query_embedding() is traced."""
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

    embed = MockEmbedding(model_name="mock-embed")
    embed.get_query_embedding("test query")

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    embed_span = None
    for trace in traces:
        for span in trace:
            if span.resource == "MockEmbedding.get_query_embedding":
                embed_span = span
                break
    assert embed_span is not None, "Expected a span with resource 'MockEmbedding.get_query_embedding'"


def test_llama_index_embedding_batch(llama_index, test_spans):
    """Test that BaseEmbedding.get_text_embedding_batch() is traced."""
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

    embed = MockEmbedding(model_name="mock-embed")
    embed.get_text_embedding_batch(["doc one", "doc two"])

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    embed_span = None
    for trace in traces:
        for span in trace:
            if span.resource == "MockEmbedding.get_text_embedding_batch":
                embed_span = span
                break
    assert embed_span is not None, "Expected a span with resource 'MockEmbedding.get_text_embedding_batch'"


def test_llama_index_agent_run(llama_index, test_spans):
    """Test that BaseWorkflowAgent.run() is traced with correct resource name."""
    from ddtrace.contrib.internal.llama_index.patch import traced_agent_run

    mock_instance = mock.MagicMock()
    mock_instance.__class__.__name__ = "MockAgent"
    mock_func = mock.MagicMock(__name__="run")
    mock_func.return_value = mock.MagicMock(response="Agent completed task.")

    traced_agent_run(mock_func, mock_instance, (), {})

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    agent_span = None
    for trace in traces:
        for span in trace:
            if span.resource == "MockAgent.run":
                agent_span = span
                break
    assert agent_span is not None, "Expected a span with resource 'MockAgent.run'"


async def test_llama_index_agent_call_tool(llama_index, test_spans):
    """Test that BaseWorkflowAgent.call_tool() is traced with correct resource name."""
    from ddtrace.contrib.internal.llama_index.patch import traced_agent_call_tool

    mock_instance = mock.MagicMock()
    mock_instance.__class__.__name__ = "MockAgent"

    async def mock_call_tool(*args, **kwargs):
        return mock.MagicMock(content="Tool result")

    mock_call_tool.__name__ = "call_tool"

    await traced_agent_call_tool(mock_call_tool, mock_instance, (), {})

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    tool_span = None
    for trace in traces:
        for span in trace:
            if span.resource == "MockAgent.call_tool":
                tool_span = span
                break
    assert tool_span is not None, "Expected a span with resource 'MockAgent.call_tool'"


def test_llama_index_predict(llama_index, test_spans):
    """Test that LLM.predict() is traced with correct resource name."""
    from ddtrace.contrib.internal.llama_index.patch import traced_llm_predict

    mock_instance = mock.MagicMock()
    mock_instance.__class__.__name__ = "OpenAI"
    mock_instance.model = "gpt-4o-mini"
    mock_instance.max_tokens = 15
    mock_func = mock.MagicMock(__name__="predict")
    mock_func.return_value = "Paris is the capital of France."

    traced_llm_predict(mock_func, mock_instance, (), {})

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    predict_span = None
    for trace in traces:
        for span in trace:
            if span.resource == "OpenAI.predict":
                predict_span = span
                break
    assert predict_span is not None, "Expected a span with resource 'OpenAI.predict'"


async def test_llama_index_apredict(llama_index, test_spans):
    """Test that LLM.apredict() is traced with correct resource name."""
    from ddtrace.contrib.internal.llama_index.patch import traced_llm_apredict

    mock_instance = mock.MagicMock()
    mock_instance.__class__.__name__ = "OpenAI"
    mock_instance.model = "gpt-4o-mini"
    mock_instance.max_tokens = 15

    async def mock_apredict(*args, **kwargs):
        return "Paris is the capital of France."

    mock_apredict.__name__ = "apredict"

    await traced_llm_apredict(mock_apredict, mock_instance, (), {})

    traces = test_spans.pop_traces()
    assert len(traces) >= 1
    predict_span = None
    for trace in traces:
        for span in trace:
            if span.resource == "OpenAI.apredict":
                predict_span = span
                break
    assert predict_span is not None, "Expected a span with resource 'OpenAI.apredict'"
