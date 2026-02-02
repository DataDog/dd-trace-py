"""LLMObs tests for claude_agent_sdk integration.

The claude-agent-sdk communicates with Claude Code via subprocess/CLI.
Response messages are yielded as a stream of typed objects:
- SystemMessage: init info (model, tools, session_id)
- AssistantMessage: responses with content blocks (TextBlock, ToolUseBlock)
- ResultMessage: completion info with usage metrics (input_tokens, output_tokens)

These tests verify that LLMObs events are properly emitted with:
- Correct model name extraction from AssistantMessage.model
- Input messages from the prompt
- Output messages from AssistantMessage.content blocks
- Token usage from ResultMessage.usage
"""

import pytest

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
    [
        dict(
            _llmobs_enabled=True,
            _llmobs_sample_rate=1.0,
            _llmobs_ml_app="<ml-app-name>",
        )
    ],
)
class TestLLMObsClaudeAgentSdk:
    """LLMObs tests for claude_agent_sdk integration.

    Tests verify that LLMObs events are emitted for query() operations
    with proper extraction of:
    - Model name from response
    - Input prompt as user message
    - Output text from AssistantMessage.content
    - Token usage from ResultMessage.usage
    """

    @pytest.mark.asyncio
    async def test_llmobs_query_extracts_content_and_usage(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, mock_internal_client, test_spans
    ):
        """Test that LLMObs properly extracts content and usage from query() responses.

        Verifies:
        - Input message is captured from prompt
        - Output message is captured from AssistantMessage.content
        - Model name is extracted from AssistantMessage.model
        - Token usage is extracted from ResultMessage.usage
        """
        prompt = "What is 2+2? Reply with just the number."

        # Call the query function and consume all messages
        messages = []
        async for message in claude_agent_sdk.query(prompt=prompt):
            messages.append(message)

        # Verify we got all 3 message types (SystemMessage, AssistantMessage, ResultMessage)
        assert len(messages) == 3

        # Verify span was created and LLMObs event was emitted
        span = test_spans.pop_traces()[0][0]
        assert span.name == "claude_agent_sdk.query"

        # Verify LLMObs writer was called with correct extracted data
        assert mock_llmobs_writer.enqueue.call_count == 1
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),  # JSON-stringified
                output_value=safe_json([{"content": "4", "role": "assistant"}]),  # JSON-stringified
                metadata={},
                token_metrics={
                    "input_tokens": EXPECTED_INPUT_TOKENS,  # 14599 (3 + 12742 + 1854)
                    "output_tokens": EXPECTED_OUTPUT_TOKENS,  # 5
                    "total_tokens": EXPECTED_TOTAL_TOKENS,  # 14604
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_query_with_options(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, mock_internal_client, test_spans
    ):
        """Test that LLMObs captures ClaudeAgentOptions parameters as metadata."""
        prompt = "Test prompt with options"

        # Create options with max_turns
        options = claude_agent_sdk.ClaudeAgentOptions(max_turns=3)

        messages = []
        async for message in claude_agent_sdk.query(prompt=prompt, options=options):
            messages.append(message)

        span = test_spans.pop_traces()[0][0]

        # Verify LLMObs captures options as metadata
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json([{"content": "4", "role": "assistant"}]),
                metadata={"max_turns": 3},  # Options captured in metadata
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
        """Test that LLMObs handles errors gracefully with empty output.

        When query() raises an error, LLMObs should still capture input
        but output messages should be empty, with error info recorded.
        """
        from unittest.mock import ANY
        from unittest.mock import patch as mock_patch

        prompt = "This will fail"

        # Mock to raise an error
        async def mock_process_query_error(self, prompt, options, transport=None):
            raise ValueError("Connection failed")
            yield  # Make it a generator

        with mock_patch(
            "claude_agent_sdk._internal.client.InternalClient.process_query",
            mock_process_query_error,
        ):
            with pytest.raises(ValueError):
                async for _ in claude_agent_sdk.query(prompt=prompt):
                    pass

        span = test_spans.pop_traces()[0][0]
        assert span.error == 1

        # Verify LLMObs still captures the input but output is empty, with error info
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json([{"content": ""}]),  # Empty message list due to error
                metadata={},
                token_metrics={},  # No usage data due to error
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
                error="builtins.ValueError",  # Error type captured
                error_message="Connection failed",  # Error message captured
                error_stack=ANY,  # Stack trace captured (use ANY since it varies)
            )
        )

    @pytest.mark.asyncio
    async def test_llmobs_client_query_captures_prompt(
        self, claude_agent_sdk, ddtrace_global_config, mock_llmobs_writer, test_spans
    ):
        """Test that ClaudeSDKClient workflow captures the prompt for LLMObs.

        ClaudeSDKClient.query() starts the span, receive_messages() finishes it.
        The span should capture input prompt and output response.
        """
        from unittest.mock import AsyncMock
        from unittest.mock import MagicMock
        from unittest.mock import patch as mock_patch

        from tests.contrib.claude_agent_sdk.utils import MOCK_MODEL

        prompt = "Hello from client!"
        mock_write = AsyncMock(return_value=None)

        # Create a mock query object with receive_messages method
        # The SDK expects raw JSON data in the format the message parser expects
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
                "message": {
                    "content": [{"type": "text", "text": "4"}],
                    "model": MOCK_MODEL,
                },
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

                # Call receive_messages to finish the span
                messages = []
                async for message in client.receive_messages():
                    messages.append(message)

        span = test_spans.pop_traces()[0][0]

        # Verify LLMObs captures prompt and response
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
        """Test that LLMObs captures Read tool use with full arguments in output messages.

        Real captured data from: .analysis/claude-agent-sdk/sample_app/artifacts/captured-tool-use.json
        When Claude uses the Read tool, the AssistantMessage contains a ToolUseBlock with:
        - id: "toolu_01C4Thx957VoSn21zERxbeQX" (real format)
        - name: "Read"
        - input: {"file_path": "/etc/hostname"}
        """
        prompt = "Read the file at /etc/hostname"

        messages = []
        async for message in claude_agent_sdk.query(prompt=prompt):
            messages.append(message)

        # Verify we got 3 messages (SystemMessage, AssistantMessage with ToolUse, ResultMessage)
        assert len(messages) == 3

        span = test_spans.pop_traces()[0][0]

        # Verify LLMObs captures tool use with full ToolCall structure (name, arguments, tool_id, type)
        # This matches the Anthropic pattern for tool_calls
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
        """Test that LLMObs captures Bash tool use with command and description arguments.

        Real captured data from: .analysis/claude-agent-sdk/sample_app/artifacts/captured-bash-grep.json
        Bash tool input structure: {"command": "echo hello", "description": "Print 'hello' to standard output"}
        """
        prompt = "Run 'echo hello' using the Bash tool"

        messages = []
        async for message in claude_agent_sdk.query(prompt=prompt):
            messages.append(message)

        assert len(messages) == 3

        span = test_spans.pop_traces()[0][0]

        # Verify Bash tool captured with full arguments including command and description
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
        """Test that LLMObs captures Grep tool use with pattern, path, and other arguments.

        Real captured data from: .analysis/claude-agent-sdk/sample_app/artifacts/captured-bash-grep.json
        Grep tool input structure: {"pattern": "def test_", "path": "tests", "output_mode": "content", "head_limit": 3}
        """
        prompt = "Use the Grep tool to search for 'def test_' in the tests directory"

        messages = []
        async for message in claude_agent_sdk.query(prompt=prompt):
            messages.append(message)

        assert len(messages) == 3

        span = test_spans.pop_traces()[0][0]

        # Verify Grep tool captured with full arguments including pattern, path, output_mode, head_limit
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
        """Test that LLMObs properly handles multi-turn conversations with tool use.

        Real captured data from: .analysis/claude-agent-sdk/sample_app/artifacts/captured-multi-turn.json

        Multi-turn in claude-agent-sdk happens when Claude uses tools internally within
        a single query() call. The message flow is:
            1. SystemMessage (init)
            2. AssistantMessage (text: "I'll read the file...")
            3. AssistantMessage (ToolUseBlock: Read tool)
            4. UserMessage (ToolResultBlock: tool result)
            5. AssistantMessage (text: final response)
            6. ResultMessage (num_turns=2, aggregated usage)

        LLMObs should capture:
        - All AssistantMessage content (text blocks AND tool use blocks)
        - Aggregated token usage from ResultMessage
        """
        prompt = "Read the file at /tmp/test.txt and tell me what's in it"

        messages = []
        async for message in claude_agent_sdk.query(prompt=prompt):
            messages.append(message)

        # Real multi-turn produces 6 messages (see docstring for breakdown)
        assert len(messages) == 6

        span = test_spans.pop_traces()[0][0]

        # Verify LLMObs captures ALL AssistantMessage content in order:
        # 1. Text explaining intent
        # 2. Tool use (Read tool with arguments)
        # 3. Final response text
        mock_llmobs_writer.enqueue.assert_called_with(
            _expected_llmobs_non_llm_span_event(
                span,
                span_kind="agent",
                input_value=safe_json([{"content": prompt, "role": "user"}]),
                output_value=safe_json(
                    [
                        # First AssistantMessage - explanation text
                        {"content": "I'll read the file at /tmp/test.txt for you.", "role": "assistant"},
                        # Second AssistantMessage - tool use with full arguments
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
                        # Third AssistantMessage - final response after tool result
                        {
                            "content": "The file at `/tmp/test.txt` does not exist. Would you like me to create it?",
                            "role": "assistant",
                        },
                    ]
                ),
                metadata={},
                token_metrics={
                    # Real captured usage: input=10, cache_creation=443, cache_read=28863
                    "input_tokens": EXPECTED_MULTI_TURN_INPUT_TOKENS,  # 29316
                    "output_tokens": EXPECTED_MULTI_TURN_OUTPUT_TOKENS,  # 147
                    "total_tokens": EXPECTED_MULTI_TURN_TOTAL_TOKENS,  # 29463
                },
                tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.claude_agent_sdk"},
            )
        )
