"""LLMObs tests for llama_index integration.

Tests verify that LLMObs span events are correctly recorded when
calling llama_index methods with LLMObs enabled.
"""

from llama_index.core.base.llms.types import ChatMessage
import pytest

import ddtrace
from tests.contrib.llama_index.utils import MockEmbedding
from tests.contrib.llama_index.utils import MockErrorLLM
from tests.contrib.llama_index.utils import MockLLM
from tests.contrib.llama_index.utils import MockLLMWithUsage
from tests.contrib.llama_index.utils import MockQueryEngine
from tests.contrib.llama_index.utils import MockRetriever
from tests.llmobs._utils import _expected_llmobs_llm_span_event


def _llmobs_tags():
    """Build tags dict with actual service/version/env from config."""
    return {
        "ml_app": "<ml-app-name>",
        "service": ddtrace.config.service or "",
        "version": ddtrace.config.version or "",
        "env": ddtrace.config.env or "",
    }


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
    """LLMObs tests for llama_index integration."""

    def test_llmobs_chat(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans):
        """Test that LLMObs records chat calls correctly."""
        llm = MockLLM()
        messages = [ChatMessage(role="user", content="Hello, world!")]
        llm.chat(messages)

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "Hello, world!", "role": "user"}],
                output_messages=[{"content": "Mock chat response", "role": "assistant"}],
                metadata={},
                tags=_llmobs_tags(),
            )
        )

    def test_llmobs_complete(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans):
        """Test that LLMObs records complete calls correctly."""
        llm = MockLLM()
        llm.complete("Hello, world!")

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "Hello, world!", "role": "user"}],
                output_messages=[{"content": "Mock completion response", "role": "assistant"}],
                metadata={},
                tags=_llmobs_tags(),
            )
        )

    def test_llmobs_chat_error(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans):
        """Test that LLMObs records errors correctly."""
        llm = MockErrorLLM()

        with pytest.raises(ValueError, match="Mock chat error"):
            llm.chat([ChatMessage(role="user", content="Hello!")])

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "Hello!", "role": "user"}],
                output_messages=[{"content": ""}],
                metadata={},
                error="builtins.ValueError",
                error_message=span.get_tag("error.message"),
                error_stack=span.get_tag("error.stack"),
                tags=_llmobs_tags(),
            )
        )

    def test_llmobs_query(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans):
        """Test that LLMObs records query engine calls correctly."""
        qe = MockQueryEngine()
        qe.query("What is the meaning of life?")

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "What is the meaning of life?", "role": "user"}],
                output_messages=[{"content": "Mock query response", "role": "assistant"}],
                metadata={},
                tags=_llmobs_tags(),
            )
        )

    def test_llmobs_retrieve(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans):
        """Test that LLMObs records retriever calls correctly."""
        ret = MockRetriever()
        ret.retrieve("test query")

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "test query", "role": "user"}],
                output_messages=[{"content": ""}],
                metadata={},
                tags=_llmobs_tags(),
            )
        )

    def test_llmobs_embedding(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans):
        """Test that LLMObs records embedding calls correctly."""
        emb = MockEmbedding()
        emb.get_query_embedding("test query")

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "test query", "role": "user"}],
                output_messages=[{"content": ""}],
                metadata={},
                tags=_llmobs_tags(),
            )
        )

    def test_llmobs_stream_chat(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans):
        """Test that LLMObs records streaming chat calls correctly."""
        llm = MockLLM()
        list(llm.stream_chat([ChatMessage(role="user", content="Hello!")]))

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "Hello!", "role": "user"}],
                output_messages=[{"content": ""}],
                metadata={},
                tags=_llmobs_tags(),
            )
        )

    def test_llmobs_chat_with_metadata(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans):
        """Test that LLMObs captures temperature and max_tokens metadata."""
        llm = MockLLM()
        llm.chat(
            [ChatMessage(role="user", content="Hello!")],
            temperature=0.7,
            max_tokens=100,
        )

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "Hello!", "role": "user"}],
                output_messages=[{"content": "Mock chat response", "role": "assistant"}],
                metadata={"temperature": 0.7, "max_tokens": 100},
                tags=_llmobs_tags(),
            )
        )

    def test_llmobs_chat_with_token_usage(self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans):
        """Test that LLMObs captures token usage metrics when available in response."""
        llm = MockLLMWithUsage()
        llm.chat([ChatMessage(role="user", content="Hello!")])

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "Hello!", "role": "user"}],
                output_messages=[{"content": "Mock chat response with usage", "role": "assistant"}],
                metadata={},
                token_metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
                tags=_llmobs_tags(),
            )
        )
