from unittest import mock

from llama_index.core.llms import ChatMessage
from llama_index.llms.openai import OpenAI
import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.contrib.llama_index.test_llama_index import _make_mock_embedding
from tests.contrib.llama_index.test_llama_index import _make_mock_query_engine
from tests.contrib.llama_index.test_llama_index import _make_mock_retriever
from tests.llmobs._utils import aiterate_stream
from tests.llmobs._utils import anext_stream
from tests.llmobs._utils import assert_llmobs_span_data
from tests.llmobs._utils import iterate_stream
from tests.llmobs._utils import next_stream


LLMOBS_GLOBAL_CONFIG = dict(
    _dd_api_key="<not-a-real-api_key>",
    _llmobs_ml_app="<ml-app-name>",
    _llmobs_enabled=True,
    _llmobs_sample_rate=1.0,
    service="tests.contrib.llama_index",
)

EXPECTED_TAGS = {
    "ml_app": "<ml-app-name>",
    "service": "tests.contrib.llama_index",
    "integration": "llama_index",
}


@pytest.mark.parametrize("ddtrace_global_config", [LLMOBS_GLOBAL_CONFIG])
class TestLLMObsLlamaIndex:
    def test_chat_completion(self, llama_index, test_spans, request_vcr):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_completion.yaml"):
            llm.chat(messages=[ChatMessage(role="user", content="Hello")])

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "Hello! How can I assist you today?", "role": "assistant"}],
            metadata={"max_tokens": 100},
            metrics={
                "input_tokens": 8,
                "output_tokens": 9,
                "total_tokens": 17,
            },
            tags=EXPECTED_TAGS,
        )

    def test_completion(self, llama_index, test_spans, request_vcr):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_complete.yaml"):
            llm.complete("What is the meaning of life?")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "What is the meaning of life?", "role": "user"}],
            output_messages=[
                {
                    "content": "The meaning of life is a profound and philosophical question that has been contemplated by",  # noqa: E501
                    "role": "assistant",
                }
            ],
            metadata={"max_tokens": 100},
            metrics={
                "input_tokens": 14,
                "output_tokens": 15,
                "total_tokens": 29,
            },
            tags=EXPECTED_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_chat_stream(self, llama_index, test_spans, request_vcr, consume_stream):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_chat_stream.yaml"):
            response = llm.stream_chat(messages=[ChatMessage(role="user", content="Hello")])
            consume_stream(response)

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "Hello! How can I assist you today?", "role": "assistant"}],
            metadata={"max_tokens": 100},
            tags=EXPECTED_TAGS,
        )

    def test_chat_error(self, llama_index, test_spans, request_vcr):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with pytest.raises(Exception):
            with request_vcr.use_cassette("llama_index_chat_error.yaml"):
                llm.chat(messages=[ChatMessage(role="user", content="Hello")])

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        span = spans[0]
        # Validate error fields are actually populated (not empty)
        assert span.get_tag("error.type"), "Expected error.type to be set"
        assert span.get_tag("error.message"), "Expected error.message to be set"
        assert span.get_tag("error.stack"), "Expected error.stack to be set"
        assert "AuthenticationError" in span.get_tag("error.type")
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "", "role": ""}],
            error={
                "type": span.get_tag("error.type"),
                "message": span.get_tag("error.message"),
                "stack": span.get_tag("error.stack"),
            },
            metadata={"max_tokens": 100},
            tags=EXPECTED_TAGS,
        )

    def test_multi_turn_conversation(self, llama_index, test_spans, request_vcr):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        messages = [
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="What is Python?"),
            ChatMessage(role="assistant", content="Python is a programming language."),
            ChatMessage(role="user", content="Tell me more."),
        ]
        with request_vcr.use_cassette("llama_index_multi_turn.yaml"):
            llm.chat(messages=messages)

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[
                {"content": "You are a helpful assistant.", "role": "system"},
                {"content": "What is Python?", "role": "user"},
                {"content": "Python is a programming language.", "role": "assistant"},
                {"content": "Tell me more.", "role": "user"},
            ],
            output_messages=[
                {
                    "content": "Certainly! Python is a high-level, interpreted programming language known for its readability and simplicity. It was created by Guido van Rossum and first released in 1991. Here are some key features and characteristics of Python:\n\n1. **Readability**: Python's syntax is designed to be clear and easy to understand, which makes it an excellent choice for beginners as well as experienced programmers.\n\n2. **Interpreted Language**: Python is an interpreted language, meaning that code is executed line by",  # noqa: E501
                    "role": "assistant",
                }
            ],
            metadata={"max_tokens": 100},
            metrics={
                "input_tokens": 39,
                "output_tokens": 100,
                "total_tokens": 139,
            },
            tags=EXPECTED_TAGS,
        )

    async def test_chat_async(self, llama_index, test_spans, request_vcr):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_completion.yaml"):
            await llm.achat(messages=[ChatMessage(role="user", content="Hello")])

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "Hello! How can I assist you today?", "role": "assistant"}],
            metadata={"max_tokens": 100},
            metrics={
                "input_tokens": 8,
                "output_tokens": 9,
                "total_tokens": 17,
            },
            tags=EXPECTED_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [aiterate_stream, anext_stream])
    async def test_chat_stream_async(self, llama_index, test_spans, request_vcr, consume_stream):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_chat_stream.yaml"):
            response = await llm.astream_chat(messages=[ChatMessage(role="user", content="Hello")])
            await consume_stream(response)

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "Hello! How can I assist you today?", "role": "assistant"}],
            metadata={"max_tokens": 100},
            tags=EXPECTED_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_complete_stream(self, llama_index, test_spans, request_vcr, consume_stream):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_complete_stream.yaml"):
            response = llm.stream_complete("What is the meaning of life?")
            consume_stream(response)

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "What is the meaning of life?", "role": "user"}],
            output_messages=[
                {
                    "content": "The meaning of life is a profound and philosophical question that has been contemplated by",  # noqa: E501
                    "role": "assistant",
                }
            ],
            metadata={"max_tokens": 100},
            tags=EXPECTED_TAGS,
        )

    @pytest.mark.parametrize("consume_stream", [aiterate_stream, anext_stream])
    async def test_complete_stream_async(self, llama_index, test_spans, request_vcr, consume_stream):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_complete_stream.yaml"):
            response = await llm.astream_complete("What is the meaning of life?")
            await consume_stream(response)

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "What is the meaning of life?", "role": "user"}],
            output_messages=[
                {
                    "content": "The meaning of life is a profound and philosophical question that has been contemplated by",  # noqa: E501
                    "role": "assistant",
                }
            ],
            metadata={"max_tokens": 100},
            tags=EXPECTED_TAGS,
        )

    async def test_complete_async(self, llama_index, test_spans, request_vcr):
        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_complete.yaml"):
            await llm.acomplete("What is the meaning of life?")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "What is the meaning of life?", "role": "user"}],
            output_messages=[
                {
                    "content": "The meaning of life is a profound and philosophical question that has been contemplated by",  # noqa: E501
                    "role": "assistant",
                }
            ],
            metadata={"max_tokens": 100},
            metrics={
                "input_tokens": 14,
                "output_tokens": 15,
                "total_tokens": 29,
            },
            tags=EXPECTED_TAGS,
        )

    def test_query_engine(self, llama_index, test_spans):
        engine = _make_mock_query_engine()
        engine.query("What is the meaning of life?")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="workflow",
            input_value="What is the meaning of life?",
            output_value="The answer is 42.",
            tags=EXPECTED_TAGS,
        )

    async def test_query_engine_async(self, llama_index, test_spans):
        engine = _make_mock_query_engine()
        await engine.aquery("What is the meaning of life?")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="workflow",
            input_value="What is the meaning of life?",
            output_value="The answer is 42.",
            tags=EXPECTED_TAGS,
        )

    def test_retriever(self, llama_index, test_spans):
        retriever = _make_mock_retriever()
        retriever.retrieve("test query")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="retrieval",
            input_value="test query",
            output_documents=[{"text": "Document text", "score": 0.95, "id": mock.ANY}],
            tags=EXPECTED_TAGS,
        )

    async def test_retriever_async(self, llama_index, test_spans):
        retriever = _make_mock_retriever()
        await retriever.aretrieve("test query")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="retrieval",
            input_value="test query",
            output_documents=[{"text": "Document text", "score": 0.95, "id": mock.ANY}],
            tags=EXPECTED_TAGS,
        )

    def test_embedding_query(self, llama_index, test_spans):
        embed = _make_mock_embedding()
        embed.get_query_embedding("test query")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="embedding",
            model_name="mock-embed",
            model_provider="unknown",
            input_documents=[{"text": "test query"}],
            output_value="[1 embedding(s) returned with size 3]",
            tags=EXPECTED_TAGS,
        )

    def test_embedding_batch(self, llama_index, test_spans):
        embed = _make_mock_embedding()
        embed.get_text_embedding_batch(["doc one", "doc two"])

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="embedding",
            model_name="mock-embed",
            model_provider="unknown",
            input_documents=[{"text": "[2 texts]"}],
            output_value="[2 embedding(s) returned with size 3]",
            tags=EXPECTED_TAGS,
        )

    async def test_embedding_query_async(self, llama_index, test_spans):
        embed = _make_mock_embedding()
        await embed.aget_query_embedding("test query")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="embedding",
            model_name="mock-embed",
            model_provider="unknown",
            input_documents=[{"text": "test query"}],
            output_value="[1 embedding(s) returned with size 3]",
            tags=EXPECTED_TAGS,
        )


def test_shadow_tags_llm_when_llmobs_disabled(tracer):
    """Verify shadow tags are set on LlamaIndex spans when LLMObs is disabled."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.llama_index import LlamaIndexIntegration

    integration = LlamaIndexIntegration(MagicMock())

    response = MagicMock()
    response.raw.usage.prompt_tokens = 14
    response.raw.usage.completion_tokens = 15
    response.raw.usage.total_tokens = 29

    with tracer.trace("llama_index.request") as span:
        span._set_attribute("llama_index.request.model", "gpt-4o-mini")
        span._set_attribute("llama_index.request.provider", "openai")
        integration._set_apm_shadow_tags(span, [], {}, response=response, operation="llm")

    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "gpt-4o-mini"
    assert span.get_tag("_dd.llmobs.model_provider") == "openai"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    assert span.get_metric("_dd.llmobs.input_tokens") == 14
    assert span.get_metric("_dd.llmobs.output_tokens") == 15
    assert span.get_metric("_dd.llmobs.total_tokens") == 29
