import pytest

from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import aiterate_stream
from tests.llmobs._utils import anext_stream
from tests.llmobs._utils import iterate_stream
from tests.llmobs._utils import next_stream


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [
        dict(
            _llmobs_enabled=True,
            _llmobs_sample_rate=1.0,
            _llmobs_ml_app="<ml-app-name>",
        )
    ],
)
class TestLLMObsLlamaIndex:
    def test_chat_completion(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr):
        """Test basic chat completion produces correct LLMObs span."""
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
            model_provider="llama_index",
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
        mock_llmobs_writer.enqueue.assert_called_with(expected)

    def test_completion(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr):
        """Test basic text completion produces correct LLMObs span."""
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_complete.yaml"):
            llm.complete("What is the meaning of life?")

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="llama_index",
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
        mock_llmobs_writer.enqueue.assert_called_with(expected)

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_chat_stream(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr, consume_stream
    ):
        """Test streamed chat completion produces correct LLMObs span."""
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
            model_provider="llama_index",
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
        mock_llmobs_writer.enqueue.assert_called_with(expected)

    def test_chat_error(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr):
        """Test that API errors still produce LLMObs spans with error info."""
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
            model_provider="llama_index",
            input_messages=[{"content": "Hello", "role": "user"}],
            output_messages=[{"content": "", "role": ""}],
            error=span.get_tag("error.type"),
            error_message=span.get_tag("error.message"),
            error_stack=span.get_tag("error.stack"),
            metadata={"max_tokens": 100},
            tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
        )
        mock_llmobs_writer.enqueue.assert_called_with(expected)

    def test_multi_turn_conversation(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr
    ):
        """Test multi-turn conversation captures all messages."""
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
            model_provider="llama_index",
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
        mock_llmobs_writer.enqueue.assert_called_with(expected)

    async def test_chat_async(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr):
        """Test async chat completion produces correct LLMObs span."""
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
            model_provider="llama_index",
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
        mock_llmobs_writer.enqueue.assert_called_with(expected)

    @pytest.mark.parametrize("consume_stream", [aiterate_stream, anext_stream])
    async def test_chat_stream_async(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr, consume_stream
    ):
        """Test async streamed chat completion produces correct LLMObs span."""
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
            model_provider="llama_index",
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
        mock_llmobs_writer.enqueue.assert_called_with(expected)

    @pytest.mark.parametrize("consume_stream", [iterate_stream, next_stream])
    def test_complete_stream(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr, consume_stream
    ):
        """Test streamed text completion produces correct LLMObs span."""
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
            model_provider="llama_index",
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
        mock_llmobs_writer.enqueue.assert_called_with(expected)

    @pytest.mark.parametrize("consume_stream", [aiterate_stream, anext_stream])
    async def test_complete_stream_async(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr, consume_stream
    ):
        """Test async streamed text completion produces correct LLMObs span."""
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
            model_provider="llama_index",
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
        mock_llmobs_writer.enqueue.assert_called_with(expected)

    async def test_complete_async(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr
    ):
        """Test async text completion produces correct LLMObs span."""
        from llama_index.llms.openai import OpenAI

        llm = OpenAI(model="gpt-4o-mini", max_tokens=100)
        with request_vcr.use_cassette("llama_index_complete.yaml"):
            await llm.acomplete("What is the meaning of life?")

        span = test_spans.pop_traces()[0][0]
        expected = _expected_llmobs_llm_span_event(
            span,
            span_kind="llm",
            model_name="gpt-4o-mini",
            model_provider="llama_index",
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
        mock_llmobs_writer.enqueue.assert_called_with(expected)
