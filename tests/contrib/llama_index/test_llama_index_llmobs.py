"""LLMObs tests for llama_index integration.

IMPORTANT: This file has the EXACT test structure required. Do NOT:
- Delete the _expected_llmobs_llm_span_event assertions
- Replace with weak assertions like 'assert len(spans) >= 1'
- Add @pytest.mark.skip decorators
- Delete tests without good reason

You MUST keep the mock_llmobs_writer.enqueue.assert_called_with() pattern.
Adjust the values based on the actual library response format.

Reference: tests/contrib/anthropic/test_anthropic_llmobs.py
Reference: tests/contrib/openai/test_openai_llmobs.py

Detected operations from analysis:
- Chat/Completion: True
- Streaming: True
- Tool calling: False
- Embeddings: True
- Async support: False
- Image generation: False

AGENT: Delete tests for operations the library does not support.
"""
import pytest

from tests.llmobs._utils import _expected_llmobs_llm_span_event


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
    """LLMObs tests for llama_index integration.

    REQUIRED: Every test MUST use mock_llmobs_writer.enqueue.assert_called_with(
        _expected_llmobs_llm_span_event(...)) to verify the exact span event data.
    """

    def test_llmobs_completion(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr
    ):
        """Test that LLMObs records are emitted for completion endpoints.

        AGENT: Study the library to understand:
        1. How to create a client
        2. What method to call for completions
        3. What the response format looks like
        Then adjust the code below accordingly.
        """
        import llama_index

        # AGENT: Adjust client initialization based on library API
        client = llama_index.Client()

        with request_vcr.use_cassette("llama_index_completion.yaml"):
            # AGENT: Adjust method call based on library API
            response = client.messages.create(
                model="REPLACE_WITH_ACTUAL_MODEL",
                max_tokens=15,
                messages=[{"role": "user", "content": "Hello, world!"}],
            )

        # DO NOT CHANGE THIS STRUCTURE - only adjust the values
        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="REPLACE_WITH_ACTUAL_MODEL",
                model_provider="llama_index",
                input_messages=[{"content": "Hello, world!", "role": "user"}],
                output_messages=[{"content": "REPLACE_WITH_ACTUAL_RESPONSE", "role": "assistant"}],
                metadata={"max_tokens": 15.0},
                token_metrics={
                    "input_tokens": 10,  # REPLACE with actual from response.usage
                    "output_tokens": 20,  # REPLACE with actual
                    "total_tokens": 30,  # REPLACE with actual
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )

    def test_llmobs_error(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr
    ):
        """Test that LLMObs records errors correctly."""
        import llama_index

        # AGENT: Adjust to trigger an error (invalid API key, bad request, etc.)
        client = llama_index.Client(api_key="invalid_api_key")

        with request_vcr.use_cassette("llama_index_error.yaml"):
            # AGENT: Adjust the exception type based on library
            with pytest.raises(Exception) as exc_info:  # REPLACE Exception with actual error type
                client.messages.create(
                    model="REPLACE_WITH_ACTUAL_MODEL",
                    max_tokens=15,
                    messages=[{"role": "user", "content": "Hello!"}],
                )

        # DO NOT CHANGE THIS STRUCTURE - only adjust the values
        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="REPLACE_WITH_ACTUAL_MODEL",
                model_provider="llama_index",
                input_messages=[{"content": "Hello!", "role": "user"}],
                output_messages=[{"content": ""}],
                error="REPLACE_WITH_ERROR_TYPE",
                error_message=span.get_tag("error.message"),
                error_stack=span.get_tag("error.stack"),
                metadata={"max_tokens": 15.0},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )

    def test_llmobs_multi_turn_conversation(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr
    ):
        """Test that LLMObs correctly records multi-turn conversations."""
        import llama_index

        client = llama_index.Client()

        with request_vcr.use_cassette("llama_index_multi_turn.yaml"):
            response = client.messages.create(
                model="REPLACE_WITH_ACTUAL_MODEL",
                max_tokens=50,
                messages=[
                    {"role": "user", "content": "My name is Alice."},
                    {"role": "assistant", "content": "Hello Alice! Nice to meet you."},
                    {"role": "user", "content": "What is my name?"},
                ],
            )

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="REPLACE_WITH_ACTUAL_MODEL",
                model_provider="llama_index",
                input_messages=[
                    {"content": "My name is Alice.", "role": "user"},
                    {"content": "Hello Alice! Nice to meet you.", "role": "assistant"},
                    {"content": "What is my name?", "role": "user"},
                ],
                output_messages=[{"content": "Your name is Alice.", "role": "assistant"}],
                metadata={"max_tokens": 50.0},
                token_metrics={
                    "input_tokens": 30,
                    "output_tokens": 10,
                    "total_tokens": 40,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )

    def test_llmobs_stream(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr
    ):
        """Test that LLMObs records streaming responses correctly.

        AGENT: If the library does not support streaming, you may delete this test.
        """
        import llama_index

        client = llama_index.Client()

        with request_vcr.use_cassette("llama_index_stream.yaml"):
            stream = client.messages.create(
                model="REPLACE_WITH_ACTUAL_MODEL",
                max_tokens=15,
                messages=[{"role": "user", "content": "Hello!"}],
                stream=True,
            )
            for chunk in stream:
                pass

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="REPLACE_WITH_ACTUAL_MODEL",
                model_provider="llama_index",
                input_messages=[{"content": "Hello!", "role": "user"}],
                output_messages=[{"content": "REPLACE_WITH_STREAMED_CONTENT", "role": "assistant"}],
                metadata={"max_tokens": 15.0},
                token_metrics={
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )

    def test_llmobs_tools(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr
    ):
        """Test that LLMObs records tool/function calls correctly.

        AGENT: If the library does not support tool calling, you may delete this test.
        """
        import llama_index

        tools = [
            {
                "name": "get_weather",
                "description": "Get the weather for a specific location",
                "input_schema": {
                    "type": "object",
                    "properties": {"location": {"type": "string"}},
                    "required": ["location"],
                },
            }
        ]

        client = llama_index.Client()

        with request_vcr.use_cassette("llama_index_tools.yaml"):
            response = client.messages.create(
                model="REPLACE_WITH_ACTUAL_MODEL",
                max_tokens=200,
                messages=[{"role": "user", "content": "What is the weather in San Francisco?"}],
                tools=tools,
            )

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="REPLACE_WITH_ACTUAL_MODEL",
                model_provider="llama_index",
                input_messages=[{"content": "What is the weather in San Francisco?", "role": "user"}],
                output_messages=[
                    {
                        "content": "",
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "name": "get_weather",
                                "arguments": {"location": "San Francisco, CA"},
                                "tool_id": "REPLACE_WITH_TOOL_ID",
                                "type": "tool_use",
                            }
                        ],
                    },
                ],
                metadata={"max_tokens": 200.0},
                token_metrics={
                    "input_tokens": 50,
                    "output_tokens": 30,
                    "total_tokens": 80,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
                tool_definitions=[
                    {
                        "name": "get_weather",
                        "description": "Get the weather for a specific location",
                        "schema": {"type": "object", "properties": {"location": {"type": "string"}}},
                    }
                ],
            )
        )

    def test_llmobs_embedding(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr
    ):
        """Test that LLMObs records embedding operations correctly.

        AGENT: If the library does not support embeddings, you may delete this test.
        """
        import llama_index

        client = llama_index.Client()

        with request_vcr.use_cassette("llama_index_embedding.yaml"):
            response = client.embeddings.create(
                input="Hello, world!",
                model="REPLACE_WITH_EMBEDDING_MODEL",
            )

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                span_kind="embedding",
                model_name="REPLACE_WITH_EMBEDDING_MODEL",
                model_provider="llama_index",
                metadata={"encoding_format": "float"},
                input_documents=[{"text": "Hello, world!"}],
                output_value="[1 embedding(s) returned with size 1536]",
                token_metrics={
                    "input_tokens": 3,
                    "output_tokens": 0,
                    "total_tokens": 3,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_completion_async(
        self, llama_index, ddtrace_global_config, mock_llmobs_writer, test_spans, request_vcr
    ):
        """Test that LLMObs records are emitted for async completion endpoints.

        AGENT: If the library does not have async support, you may delete this test.
        """
        import llama_index

        client = llama_index.AsyncClient()

        with request_vcr.use_cassette("llama_index_completion.yaml"):
            response = await client.messages.create(
                model="REPLACE_WITH_ACTUAL_MODEL",
                max_tokens=15,
                messages=[{"role": "user", "content": "Hello, world!"}],
            )

        span = test_spans.pop_traces()[0][0]
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_llm_span_event(
                span,
                model_name="REPLACE_WITH_ACTUAL_MODEL",
                model_provider="llama_index",
                input_messages=[{"content": "Hello, world!", "role": "user"}],
                output_messages=[{"content": "REPLACE_WITH_ACTUAL_RESPONSE", "role": "assistant"}],
                metadata={"max_tokens": 15.0},
                token_metrics={
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.llama_index"},
            )
        )