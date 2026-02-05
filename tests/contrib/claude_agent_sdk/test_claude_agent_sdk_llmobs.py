from unittest.mock import ANY

import pytest

from ddtrace.llmobs._utils import safe_json
from tests.contrib.claude_agent_sdk.utils import EXPECTED_QUERY_USAGE
from tests.contrib.claude_agent_sdk.utils import EXPECTED_SYSTEM_MESSAGE_DATA
from tests.contrib.claude_agent_sdk.utils import MOCK_BASH_TOOL_ID
from tests.contrib.claude_agent_sdk.utils import MOCK_BASH_TOOL_INPUT
from tests.contrib.claude_agent_sdk.utils import MOCK_GREP_TOOL_ID
from tests.contrib.claude_agent_sdk.utils import MOCK_GREP_TOOL_INPUT
from tests.contrib.claude_agent_sdk.utils import MOCK_READ_TOOL_ID
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


class TestLLMObsClaudeAgentSdk:
    async def test_llmobs_query_extracts_content_and_usage(
        self, claude_agent_sdk, llmobs_events, mock_internal_client, test_spans
    ):
        prompt = "What is 2+2? Reply with just the number."
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        expected_event = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="agent",
            input_value=safe_json([{"content": prompt, "role": "user"}]),
            output_value=safe_json(
                [
                    {
                        "content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA),
                        "role": "system",
                    },
                    {"content": "4", "role": "assistant"},
                    {"content": "4", "role": "system"},
                ]
            ),
            metadata={},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags={"ml_app": "unnamed-ml-app", "service": "tests.llmobs"},
        )

        assert llmobs_events[0] == expected_event

    async def test_llmobs_query_with_options(self, claude_agent_sdk, llmobs_events, mock_internal_client, test_spans):
        prompt = "Test prompt with options"
        options = claude_agent_sdk.ClaudeAgentOptions(max_turns=3)

        async for _ in claude_agent_sdk.query(prompt=prompt, options=options):
            pass

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        expected_event = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="agent",
            input_value=safe_json([{"content": prompt, "role": "user"}]),
            output_value=safe_json(
                [
                    {
                        "content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA),
                        "role": "system",
                    },
                    {"content": "4", "role": "assistant"},
                    {"content": "4", "role": "system"},
                ]
            ),
            metadata={"max_turns": 3},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags={"ml_app": "unnamed-ml-app", "service": "tests.llmobs"},
        )

        assert llmobs_events[0] == expected_event

    async def test_llmobs_query_error_no_output(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_error, test_spans
    ):
        prompt = "This will fail"

        with pytest.raises(ValueError):
            async for _ in claude_agent_sdk.query(prompt=prompt):
                pass

        span = test_spans.pop_traces()[0][0]
        assert span.error == 1
        assert len(llmobs_events) == 1

        expected_event = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="agent",
            input_value=safe_json([{"content": prompt, "role": "user"}]),
            output_value=safe_json([{"content": ""}]),
            metadata={},
            token_metrics={},
            tags={"ml_app": "unnamed-ml-app", "service": "tests.llmobs"},
            error="builtins.ValueError",
            error_message="Connection failed",
            error_stack=ANY,
        )

        assert llmobs_events[0] == expected_event

    async def test_llmobs_client_query_captures_prompt(self, mock_client, llmobs_events, test_spans):
        prompt = "Hello from client!"

        await mock_client.query(prompt=prompt)
        async for _ in mock_client.receive_messages():
            pass

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        expected_event = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="agent",
            input_value=safe_json([{"content": prompt, "role": "user"}]),
            output_value=safe_json(
                [
                    {
                        "content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA),
                        "role": "system",
                    },
                    {"content": "4", "role": "assistant"}
                ]
            ),
            metadata={
                "after_context": {"categories": {}, "used_tokens": None, "total_tokens": None},
                "before_context": {"categories": {}, "used_tokens": None, "total_tokens": None},
            },
            token_metrics={
                "input_tokens": 14599,
                "output_tokens": 5,
                "total_tokens": 14604,
                "cache_write_input_tokens": 12742,
                "cache_read_input_tokens": 1854,
            },
            tags={"ml_app": "unnamed-ml-app", "service": "tests.llmobs"},
        )

        assert llmobs_events[0] == expected_event

    async def test_llmobs_query_with_read_tool_use(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_tool_use, test_spans
    ):
        prompt = "Read the file at /etc/hostname"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        expected_event = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="agent",
            input_value=safe_json([{"content": prompt, "role": "user"}]),
            output_value=safe_json(
                [
                    {
                        "content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA),
                        "role": "system",
                    },
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
                    },
                    {"content": "4", "role": "system"},
                ]
            ),
            metadata={},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags={"ml_app": "unnamed-ml-app", "service": "tests.llmobs"},
        )

        assert llmobs_events[0] == expected_event

    async def test_llmobs_query_with_bash_tool_use(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_bash_tool, test_spans
    ):
        prompt = "Run 'echo hello' using the Bash tool"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        expected_event = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="agent",
            input_value=safe_json([{"content": prompt, "role": "user"}]),
            output_value=safe_json(
                [
                    {
                        "content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA),
                        "role": "system",
                    },
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
                    },
                    {"content": "4", "role": "system"},
                ]
            ),
            metadata={},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags={"ml_app": "unnamed-ml-app", "service": "tests.llmobs"},
        )

        assert llmobs_events[0] == expected_event

    async def test_llmobs_query_with_grep_tool_use(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_grep_tool, test_spans
    ):
        prompt = "Use the Grep tool to search for 'def test_' in the tests directory"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        span = test_spans.pop_traces()[0][0]
        assert len(llmobs_events) == 1

        expected_event = _expected_llmobs_non_llm_span_event(
            span,
            span_kind="agent",
            input_value=safe_json([{"content": prompt, "role": "user"}]),
            output_value=safe_json(
                [
                    {
                        "content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA),
                        "role": "system",
                    },
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
                    },
                    {"content": "4", "role": "system"},
                ]
            ),
            metadata={},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags={"ml_app": "unnamed-ml-app", "service": "tests.llmobs"},
        )

        assert llmobs_events[0] == expected_event
