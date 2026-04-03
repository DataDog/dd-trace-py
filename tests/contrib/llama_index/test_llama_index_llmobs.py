from unittest import mock

import pytest

from tests.contrib.llama_index.test_llama_index import _make_mock_embedding
from tests.contrib.llama_index.test_llama_index import _make_mock_query_engine
from tests.contrib.llama_index.test_llama_index import _make_mock_retriever
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event
from tests.llmobs._utils import aiterate_stream
from tests.llmobs._utils import anext_stream
from tests.llmobs._utils import iterate_stream
from tests.llmobs._utils import next_stream


class TestLLMObsLlamaIndex:
    def test_chat_completion(self, llama_index, llmobs_events, test_spans, request_vcr):
        from llama_index.core.llms import ChatMessage
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_completion.yaml"):
            llm.chat(messages=[ChatMessage(role="user", content="Hello")])

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "Hi there!", "role": "assistant"}],
            metadata={"max_tokens": 100},
            token_metrics={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    def test_completion(self, llama_index, llmobs_events, test_spans, request_vcr):
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_complete.yaml"):
            llm.complete("What is the meaning of life?")

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "What is the meaning of life?", "role": "user"}],
            output_messages=[{"content": "42", "role": "assistant"}],
            metadata={"max_tokens": 100},
            token_metrics={
                "input_tokens": 8,
                "output_tokens": 3,
                "total_tokens": 11,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_chat_stream(self, llama_index, llmobs_events, test_spans, request_vcr, consume_stream):
        from llama_index.core.llms import ChatMessage
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_chat_stream.yaml"):
            response = llm.stream_chat(messages=[ChatMessage(role="user", content="Hello")])
            consume_stream(response)

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "Hi there!", "role": "assistant"}],
            metadata={"max_tokens": 100},
            token_metrics={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    def test_chat_error(self, llama_index, llmobs_events, test_spans, request_vcr):
        from llama_index.core.llms import ChatMessage
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with pytest.raises(Exception):
            with request_vcr.use_cassette("llama_index_chat_error.yaml"):
                llm.chat(messages=[ChatMessage(role="user", content="Hello")])

        span = test_spans.pop_traces()[0][0]
        # Validate error fields are actually populated (not empty)
        assert span.get_tag("error.type"), "Expected error.type to be set"
        assert span.get_tag("error.message"), "Expected error.message to be set"
        assert span.get_tag("error.stack"), "Expected error.stack to be set"
        assert "AuthenticationError" in span.get_tag("error.type")
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "", "role": ""}],
            error=span.get_tag("error.type"),
            error_message=span.get_tag("error.message"),
            error_stack=span.get_tag("error.stack"),
            metadata={"max_tokens": 100},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    def test_multi_turn_conversation(self, llama_index, llmobs_events, test_spans, request_vcr):
        from llama_index.core.llms import ChatMessage
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        messages = [
            ChatMessage(role="system", content="You are a helpful assistant."),
            ChatMessage(role="user", content="What is Python?"),
            ChatMessage(role="assistant", content="Python is a programming language."),
            ChatMessage(role="user", content="Tell me more."),
        ]
        with request_vcr.use_cassette("llama_index_multi_turn.yaml"):
            llm.chat(messages=messages)

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
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
                    "content": "Python is a high-level, interpreted programming language.",
                    "role": "assistant",
                }
            ],
            metadata={"max_tokens": 100},
            token_metrics={
                "input_tokens": 30,
                "output_tokens": 15,
                "total_tokens": 45,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    async def test_chat_async(self, llama_index, llmobs_events, test_spans, request_vcr):
        from llama_index.core.llms import ChatMessage
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_completion.yaml"):
            await llm.achat(messages=[ChatMessage(role="user", content="Hello")])

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "Hi there!", "role": "assistant"}],
            metadata={"max_tokens": 100},
            token_metrics={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    @pytest.mark.parametrize("consume_stream", [aiterate_stream, anext_stream])
    async def test_chat_stream_async(self, llama_index, llmobs_events, test_spans, request_vcr, consume_stream):
        from llama_index.core.llms import ChatMessage
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_chat_stream.yaml"):
            response = await llm.astream_chat(messages=[ChatMessage(role="user", content="Hello")])
            await consume_stream(response)

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "Hi there!", "role": "assistant"}],
            metadata={"max_tokens": 100},
            token_metrics={
                "input_tokens": 10,
                "output_tokens": 5,
                "total_tokens": 15,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_complete_stream(self, llama_index, llmobs_events, test_spans, request_vcr, consume_stream):
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_complete_stream.yaml"):
            response = llm.stream_complete("What is the meaning of life?")
            consume_stream(response)

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "What is the meaning of life?", "role": "user"}],
            output_messages=[{"content": "42", "role": "assistant"}],
            metadata={"max_tokens": 100},
            token_metrics={
                "input_tokens": 8,
                "output_tokens": 3,
                "total_tokens": 11,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    @pytest.mark.parametrize("consume_stream", [aiterate_stream, anext_stream])
    async def test_complete_stream_async(self, llama_index, llmobs_events, test_spans, request_vcr, consume_stream):
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_complete_stream.yaml"):
            response = await llm.astream_complete("What is the meaning of life?")
            await consume_stream(response)

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "What is the meaning of life?", "role": "user"}],
            output_messages=[{"content": "42", "role": "assistant"}],
            metadata={"max_tokens": 100},
            token_metrics={
                "input_tokens": 8,
                "output_tokens": 3,
                "total_tokens": 11,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    async def test_complete_async(self, llama_index, llmobs_events, test_spans, request_vcr):
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_complete.yaml"):
            await llm.acomplete("What is the meaning of life?")

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="openai",
            input_messages=[{"content": "What is the meaning of life?", "role": "user"}],
            output_messages=[{"content": "42", "role": "assistant"}],
            metadata={"max_tokens": 100},
            token_metrics={
                "input_tokens": 8,
                "output_tokens": 3,
                "total_tokens": 11,
            },
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    def test_query_engine(self, llama_index, llmobs_events, test_spans):
        engine = _make_mock_query_engine()
        engine.query("What is the meaning of life?")

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="workflow",
            input_value="What is the meaning of life?",
            output_value="The answer is 42.",
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    async def test_query_engine_async(self, llama_index, llmobs_events, test_spans):
        engine = _make_mock_query_engine()
        await engine.aquery("What is the meaning of life?")

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="workflow",
            input_value="What is the meaning of life?",
            output_value="The answer is 42.",
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    def test_retriever(self, llama_index, llmobs_events, test_spans):
        retriever = _make_mock_retriever()
        retriever.retrieve("test query")

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="retrieval",
            input_value="test query",
            output_documents=[{"text": "Document text", "score": 0.95, "id": mock.ANY}],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    async def test_retriever_async(self, llama_index, llmobs_events, test_spans):
        retriever = _make_mock_retriever()
        await retriever.aretrieve("test query")

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="retrieval",
            input_value="test query",
            output_documents=[{"text": "Document text", "score": 0.95, "id": mock.ANY}],
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    def test_embedding_query(self, llama_index, llmobs_events, test_spans):
        embed = _make_mock_embedding()
        embed.get_query_embedding("test query")

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="mock-embed",
            model_provider="unknown",
            input_documents=[{"text": "test query"}],
            output_value="[1 embedding(s) returned with size 3]",
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    def test_embedding_batch(self, llama_index, llmobs_events, test_spans):
        embed = _make_mock_embedding()
        embed.get_text_embedding_batch(["doc one", "doc two"])

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="mock-embed",
            model_provider="unknown",
            input_documents=[{"text": "[2 texts]"}],
            output_value="[2 embedding(s) returned with size 3]",
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected

    async def test_embedding_query_async(self, llama_index, llmobs_events, test_spans):
        embed = _make_mock_embedding()
        await embed.aget_query_embedding("test query")

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="embedding",
            model_name="mock-embed",
            model_provider="unknown",
            input_documents=[{"text": "test query"}],
            output_value="[1 embedding(s) returned with size 3]",
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        assert len(llmobs_events) == 1
        assert llmobs_events[0] == expected
