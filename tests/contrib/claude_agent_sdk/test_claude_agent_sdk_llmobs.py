import pytest
from unittest.mock import ANY
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch as mock_patch

from ddtrace.llmobs._utils import safe_json
from tests.contrib.claude_agent_sdk.utils import EXPECTED_INPUT_TOKENS
from tests.contrib.claude_agent_sdk.utils import EXPECTED_MULTI_TURN_INPUT_TOKENS
from tests.contrib.claude_agent_sdk.utils import EXPECTED_MULTI_TURN_OUTPUT_TOKENS
from tests.contrib.claude_agent_sdk.utils import EXPECTED_MULTI_TURN_TOTAL_TOKENS
from tests.contrib.claude_agent_sdk.utils import EXPECTED_OUTPUT_TOKENS
from tests.contrib.claude_agent_sdk.utils import EXPECTED_TOTAL_TOKENS
from tests.contrib.claude_agent_sdk.utils import MOCK_BASH_TOOL_ID
from tests.contrib.claude_agent_sdk.utils import MOCK_BASH_TOOL_INPUT
from tests.contrib.claude_agent_sdk.utils import MOCK_GREP_TOOL_ID
from tests.contrib.claude_agent_sdk.utils import MOCK_GREP_TOOL_INPUT
from tests.contrib.claude_agent_sdk.utils import MOCK_MODEL
from tests.contrib.claude_agent_sdk.utils import MOCK_MULTI_TURN_TOOL_ID
from tests.contrib.claude_agent_sdk.utils import MOCK_READ_TOOL_ID
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")],
)
class TestLLMObsClaudeAgentSdk:
    @pytest.mark.asyncio
    async def test_llmobs_query_extracts_content_and_usage(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, mock_internal_client, test_spans
    ):
        prompt = "What is 2+2? Reply with just the number."
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        span = test_spans.pop_traces()[0][0]
        assert span.name == "claude_agent_sdk.query"
        assert mock_llmobs_writer.enqueue.call_count == 1

        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json([{"content": "4", "role": "assistant"}]),
                metadata={},
                token_metrics={
                    "input_tokens": EXPECTED_INPUT_TOKENS,
                    "output_tokens": EXPECTED_OUTPUT_TOKENS,
                    "total_tokens": EXPECTED_TOTAL_TOKENS,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_query_with_options(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, mock_internal_client, test_spans
    ):
        prompt = "Test prompt with options"
        options = claude_agent_sdk.ClaudeAgentOptions(max_turns=3)

        async for _ in claude_agent_sdk.query(prompt=prompt, options=options):
            pass

        span = test_spans.pop_traces()[0][0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json([{"content": "4", "role": "assistant"}]),
                metadata={"max_turns": 3},
                token_metrics={
                    "input_tokens": EXPECTED_INPUT_TOKENS,
                    "output_tokens": EXPECTED_OUTPUT_TOKENS,
                    "total_tokens": EXPECTED_TOTAL_TOKENS,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_query_error_no_output(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, test_spans
    ):
        prompt = "This will fail"

        async def mock_process_query_error(self, prompt, options, transport=None):
            raise ValueError("Connection failed")
            yield

        with mock_patch(
            "claude_agent_sdk._internal.client.InternalClient.process_query", mock_process_query_error
        ):
            with pytest.raises(ValueError):
                async for _ in claude_agent_sdk.query(prompt=prompt):
                    pass

        span = test_spans.pop_traces()[0][0]
        assert span.error == 1

        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json([{"content": ""}]),
                metadata={},
                token_metrics={},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
                error="builtins.ValueError",
                error_message="Connection failed",
                error_stack=ANY,
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_client_query_captures_prompt(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, test_spans
    ):
        prompt = "Hello from client!"
        mock_write = AsyncMock(return_value=None)

        async def mock_receive_messages():
            yield {
                "type": "system",
                "subtype": "init",
                "cwd": "/test/path",
                "session_id": "test-session-id",
                "tools": ["Task", "Bash", "Read"],
                "model": MOCK_MODEL,
            }
            yield {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "4"}], "model": MOCK_MODEL},
            }
            yield {
                "type": "result",
                "subtype": "success",
                "duration_ms": 100,
                "duration_api_ms": 90,
                "is_error": False,
                "num_turns": 1,
                "session_id": "test-session-id",
                "usage": {
                    "input_tokens": 3,
                    "cache_creation_input_tokens": 12742,
                    "cache_read_input_tokens": 1854,
                    "output_tokens": 5,
                },
            }

        mock_query = MagicMock()
        mock_query.receive_messages = mock_receive_messages

        with mock_patch.object(claude_agent_sdk.ClaudeSDKClient, "connect", new_callable=AsyncMock):
            with mock_patch.object(claude_agent_sdk.ClaudeSDKClient, "disconnect", new_callable=AsyncMock):
                client = claude_agent_sdk.ClaudeSDKClient()
                client._query = mock_query
                client._transport = MagicMock()
                client._transport.write = mock_write

                await client.query(prompt=prompt)
                async for _ in client.receive_messages():
                    pass

        span = test_spans.pop_traces()[0][0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json([{"content": "4", "role": "assistant"}]),
                metadata={},
                token_metrics={"input_tokens": 14599, "output_tokens": 5, "total_tokens": 14604},
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_query_with_read_tool_use(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, mock_internal_client_tool_use, test_spans
    ):
        prompt = "Read the file at /etc/hostname"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        span = test_spans.pop_traces()[0][0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json(
                    [
                        {
                            "content": "",
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "name": "Read",
                                    "arguments": {"file_path": "/etc/hostname"},
                                    "tool_id": MOCK_READ_TOOL_ID,
                                    "type": "tool_use",
                                }
                            ],
                        }
                    ]
                ),
                metadata={},
                token_metrics={
                    "input_tokens": EXPECTED_INPUT_TOKENS,
                    "output_tokens": EXPECTED_OUTPUT_TOKENS,
                    "total_tokens": EXPECTED_TOTAL_TOKENS,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_query_with_bash_tool_use(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, mock_internal_client_bash_tool, test_spans
    ):
        prompt = "Run 'echo hello' using the Bash tool"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        span = test_spans.pop_traces()[0][0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json(
                    [
                        {
                            "content": "",
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "name": "Bash",
                                    "arguments": MOCK_BASH_TOOL_INPUT,
                                    "tool_id": MOCK_BASH_TOOL_ID,
                                    "type": "tool_use",
                                }
                            ],
                        }
                    ]
                ),
                metadata={},
                token_metrics={
                    "input_tokens": EXPECTED_INPUT_TOKENS,
                    "output_tokens": EXPECTED_OUTPUT_TOKENS,
                    "total_tokens": EXPECTED_TOTAL_TOKENS,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_query_with_grep_tool_use(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, mock_internal_client_grep_tool, test_spans
    ):
        prompt = "Use the Grep tool to search for 'def test_' in the tests directory"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        span = test_spans.pop_traces()[0][0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json(
                    [
                        {
                            "content": "",
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "name": "Grep",
                                    "arguments": MOCK_GREP_TOOL_INPUT,
                                    "tool_id": MOCK_GREP_TOOL_ID,
                                    "type": "tool_use",
                                }
                            ],
                        }
                    ]
                ),
                metadata={},
                token_metrics={
                    "input_tokens": EXPECTED_INPUT_TOKENS,
                    "output_tokens": EXPECTED_OUTPUT_TOKENS,
                    "total_tokens": EXPECTED_TOTAL_TOKENS,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_query_multi_turn_with_tool_use(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, mock_internal_client_multi_turn, test_spans
    ):
        prompt = "Read the file at /tmp/test.txt and tell me what's in it"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        span = test_spans.pop_traces()[0][0]
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json(
                    [
                        {"content": "I'll read the file at /tmp/test.txt for you.", "role": "assistant"},
                        {
                            "content": "",
                            "role": "assistant",
                            "tool_calls": [
                                {
                                    "name": "Read",
                                    "arguments": {"file_path": "/tmp/test.txt"},
                                    "tool_id": MOCK_MULTI_TURN_TOOL_ID,
                                    "type": "tool_use",
                                }
                            ],
                        },
                        {
                            "content": "The file at `/tmp/test.txt` does not exist. Would you like me to create it?",
                            "role": "assistant",
                        },
                    ]
                ),
                metadata={},
                token_metrics={
                    "input_tokens": EXPECTED_MULTI_TURN_INPUT_TOKENS,
                    "output_tokens": EXPECTED_MULTI_TURN_OUTPUT_TOKENS,
                    "total_tokens": EXPECTED_MULTI_TURN_TOTAL_TOKENS,
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
            )
        )
