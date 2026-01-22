"""LLMObs tests for llama_index integration.

Tests validate LLMObs event data extraction for llama-index operations.
Uses MockLLM which doesn't require API calls or VCR cassettes.

Note: MockLLM overrides complete() directly, so that method is not traced when using MockLLM.
Chat is traced because it's inherited from CustomLLM.

Note: MockLLM does not return token usage data, so token_metrics={} is expected.
Real LLM providers (OpenAI, etc.) would return token usage in response.additional_kwargs
or response.raw.usage, which would be extracted by _extract_usage_from_response.
"""

from typing import Any

import pytest

from tests.llmobs._utils import _expected_llmobs_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [
        {
            "_llmobs_enabled": True,
            "_llmobs_sample_rate": 1.0,
            "_llmobs_ml_app": "<ml-app-name>",
        }
    ],
)
class TestLLMObsLlamaIndex:
    """LLMObs tests for llama_index integration.

    REQUIRED: Every test MUST use mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(...)) to verify the exact span event data.
    """

    def test_llmobs_chat(
        self, llama_index: Any, ddtrace_global_config: Any, mock_llmobs_writer: Any, test_spans: Any
    ) -> None:
        """Test that LLMObs records chat operations correctly."""
        _ChatMessage = llama_index.llms.ChatMessage  # noqa: N806
        _MessageRole = llama_index.llms.MessageRole  # noqa: N806
        _MockLLM = llama_index.llms.mock.MockLLM  # noqa: N806

        llm = _MockLLM()
        messages = [_ChatMessage(role=_MessageRole.USER, content="Hello!")]

        response = llm.chat(messages)

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "Hello!", "role": "user"}],
                output_messages=[{"content": response.message.content, "role": "assistant"}],
                metadata={},
                token_metrics={},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )

    def test_llmobs_embedding(
        self, llama_index: Any, ddtrace_global_config: Any, mock_llmobs_writer: Any, test_spans: Any
    ) -> None:
        """Test that LLMObs records embedding operations correctly.

        Note: Uses span_kind="embedding" with _expected_llmobs_llm_span_event.
        """
        _MockEmbedding = llama_index.MockEmbedding  # noqa: N806

        embed = _MockEmbedding(embed_dim=384)

        embed.get_text_embedding("Hello, world!")  # Result unused - we verify via span

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name="",
                model_provider="llama_index",
                input_documents=[{"text": "Hello, world!"}],
                output_value="[1 embedding(s) returned with size 384]",
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )

    def test_llmobs_multi_turn_chat(
        self, llama_index: Any, ddtrace_global_config: Any, mock_llmobs_writer: Any, test_spans: Any
    ) -> None:
        """Test that LLMObs correctly records multi-turn conversations."""
        _ChatMessage = llama_index.llms.ChatMessage  # noqa: N806
        _MessageRole = llama_index.llms.MessageRole  # noqa: N806
        _MockLLM = llama_index.llms.mock.MockLLM  # noqa: N806

        llm = _MockLLM()
        messages = [
            _ChatMessage(role=_MessageRole.USER, content="My name is Alice."),
            _ChatMessage(role=_MessageRole.ASSISTANT, content="Hello Alice!"),
            _ChatMessage(role=_MessageRole.USER, content="What is my name?"),
        ]

        response = llm.chat(messages)

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="",
                model_provider="llama_index",
                input_messages=[
                    {"content": "My name is Alice.", "role": "user"},
                    {"content": "Hello Alice!", "role": "assistant"},
                    {"content": "What is my name?", "role": "user"},
                ],
                output_messages=[{"content": response.message.content, "role": "assistant"}],
                metadata={},
                token_metrics={},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_achat(
        self, llama_index: Any, ddtrace_global_config: Any, mock_llmobs_writer: Any, test_spans: Any
    ) -> None:
        """Test that LLMObs records async chat operations correctly.

        Note: achat internally calls chat, so we get 2 LLMObs events.
        We verify the achat event specifically.
        """
        _ChatMessage = llama_index.llms.ChatMessage  # noqa: N806
        _MessageRole = llama_index.llms.MessageRole  # noqa: N806
        _MockLLM = llama_index.llms.mock.MockLLM  # noqa: N806

        llm = _MockLLM()
        messages = [_ChatMessage(role=_MessageRole.USER, content="Hello async!")]

        response = await llm.achat(messages)

        # achat internally calls chat, so we get 2 LLMObs events
        traces = test_spans.pop_traces()
        all_spans = [span for trace in traces for span in trace]
        achat_span = next(s for s in all_spans if s.name == "MockLLM.achat")

        assert mock_llmobs_writer.enqueue.call_count == 2
        # The second call is for achat (the outer span)
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                achat_span,
                model_name="",
                model_provider="llama_index",
                input_messages=[{"content": "Hello async!", "role": "user"}],
                output_messages=[{"content": response.message.content, "role": "assistant"}],
                metadata={},
                token_metrics={},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_aget_text_embedding(
        self, llama_index: Any, ddtrace_global_config: Any, mock_llmobs_writer: Any, test_spans: Any
    ) -> None:
        """Test that LLMObs records async embedding operations correctly."""
        _MockEmbedding = llama_index.MockEmbedding  # noqa: N806

        embed = _MockEmbedding(embed_dim=384)

        await embed.aget_text_embedding("Hello async world!")

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name="",
                model_provider="llama_index",
                input_documents=[{"text": "Hello async world!"}],
                output_value="[1 embedding(s) returned with size 384]",
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [
        {
            "_llmobs_enabled": True,
            "_llmobs_sample_rate": 1.0,
            "_llmobs_ml_app": "<ml-app-name>",
        }
    ],
)
class TestLLMObsLlamaIndexOpenAI:
    """LLMObs tests with real OpenAI embedding model via VCR cassettes.

    These tests verify token usage extraction and correct content handling
    with a production-like backend using OpenAI embeddings.

    Note: OpenAI LLM (chat/complete) is not instrumented because llama_index.llms.openai.OpenAI
    has its own implementation that doesn't go through CustomLLM.chat/complete which is what
    we wrap. Embeddings work because OpenAIEmbedding inherits from BaseEmbedding which we wrap.

    To record new cassettes:
        OPENAI_API_KEY=<real-key> pytest tests/contrib/llama_index/test_llama_index_llmobs.py -k OpenAI
    """

    def test_llmobs_openai_embedding_with_token_metrics(
        self,
        llama_index: Any,
        openai_embedding: Any,
        ddtrace_global_config: Any,
        mock_llmobs_writer: Any,
        test_spans: Any,
        request_vcr: Any,
    ) -> None:
        """Test that LLMObs correctly records OpenAI embedding operations.

        Uses VCR cassette to replay a real OpenAI embedding API response.
        """
        text = "Hello, world!"

        with request_vcr.use_cassette("llama_index_openai_embedding.yaml"):
            result = openai_embedding.get_text_embedding(text)

        # Find the embedding span (subprocess integration may create command_execution spans)
        traces = test_spans.pop_traces()
        all_spans = [span for trace in traces for span in trace]
        embedding_spans = [s for s in all_spans if "embedding" in s.name.lower()]
        assert len(embedding_spans) == 1, f"Expected 1 embedding span, found: {[s.name for s in all_spans]}"
        span = embedding_spans[0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name="text-embedding-3-small",
                model_provider="llama_index",
                input_documents=[{"text": text}],
                output_value=f"[1 embedding(s) returned with size {len(result)}]",
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [
        {
            "_llmobs_enabled": True,
            "_llmobs_sample_rate": 1.0,
            "_llmobs_ml_app": "<ml-app-name>",
        }
    ],
)
class TestLLMObsLlamaIndexOpenAIChat:
    """LLMObs tests with real OpenAI chat model via VCR cassettes.

    These tests verify token usage extraction and correct content handling
    using a custom LLM that wraps OpenAI and goes through CustomLLM.chat().

    The llama_index.llms.openai.OpenAI class doesn't go through CustomLLM.chat/complete
    (it has its own implementation), so we use openai_custom_llm fixture that:
    1. Inherits from CustomLLM (so it goes through our wrapped methods)
    2. Delegates to OpenAI for actual API calls (recorded via VCR)
    3. Returns ChatResponse with proper token usage in additional_kwargs

    Note: Direct complete() calls on CustomLLM subclasses are NOT traced because
    complete() is an abstract method that must be overridden, and overriding bypasses
    our wrapper on CustomLLM.complete. The chat() method IS traced because it's not
    abstract and calls self.complete() internally.

    To record new cassettes:
        OPENAI_API_KEY=<real-key> pytest tests/contrib/llama_index/test_llama_index_llmobs.py -k OpenAIChat
    """

    def test_llmobs_openai_chat_with_token_metrics(
        self,
        llama_index: Any,
        openai_custom_llm: Any,
        ddtrace_global_config: Any,
        mock_llmobs_writer: Any,
        test_spans: Any,
        request_vcr: Any,
    ) -> None:
        """Test that LLMObs correctly records chat operations with token metrics.

        Uses a custom LLM wrapper (from fixture) that goes through CustomLLM.chat()
        and returns proper token usage data from OpenAI API responses.

        This test verifies:
        - Input/output content extraction from chat messages
        - Token usage metrics (input_tokens, output_tokens, total_tokens)
        - Model name and provider metadata
        - Integration with real OpenAI API via VCR cassette
        """
        from llama_index.core.llms import ChatMessage
        from llama_index.core.llms import MessageRole

        llm = openai_custom_llm
        messages = [ChatMessage(role=MessageRole.USER, content="Hello! What is 2+2?")]

        with request_vcr.use_cassette("llama_index_openai_chat.yaml"):
            response = llm.chat(messages)

        # Verify response was returned correctly
        assert response is not None
        assert response.message.content == "2+2 equals 4."

        # Find the LLM chat span (subprocess integration may create command_execution spans)
        traces = test_spans.pop_traces()
        all_spans = [span for trace in traces for span in trace]
        llm_spans = [s for s in all_spans if "chat" in s.name.lower()]
        assert len(llm_spans) == 1, f"Expected 1 LLM chat span, found: {[s.name for s in all_spans]}"
        span = llm_spans[0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="gpt-4o-mini",
                model_provider="llama_index",
                input_messages=[{"content": "Hello! What is 2+2?", "role": "user"}],
                output_messages=[{"content": response.message.content, "role": "assistant"}],
                metadata={},
                token_metrics={"input_tokens": 12, "output_tokens": 8, "total_tokens": 20},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )
