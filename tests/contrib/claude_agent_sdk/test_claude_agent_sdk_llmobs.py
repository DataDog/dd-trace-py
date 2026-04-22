from unittest.mock import ANY

import claude_agent_sdk
import pytest

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import safe_json
from tests.contrib.claude_agent_sdk.utils import EXPECTED_ASSISTANT_USAGE
from tests.contrib.claude_agent_sdk.utils import EXPECTED_QUERY_USAGE
from tests.contrib.claude_agent_sdk.utils import EXPECTED_SYSTEM_MESSAGE_DATA
from tests.contrib.claude_agent_sdk.utils import MOCK_ASSISTANT_MESSAGE_ERROR
from tests.contrib.claude_agent_sdk.utils import MOCK_BASH_TOOL_ID
from tests.contrib.claude_agent_sdk.utils import MOCK_BASH_TOOL_INPUT
from tests.contrib.claude_agent_sdk.utils import MOCK_FINAL_ASSISTANT_TEXT
from tests.contrib.claude_agent_sdk.utils import MOCK_GREP_TOOL_ID
from tests.contrib.claude_agent_sdk.utils import MOCK_GREP_TOOL_INPUT
from tests.contrib.claude_agent_sdk.utils import MOCK_MODEL
from tests.contrib.claude_agent_sdk.utils import MOCK_READ_TOOL_ID
from tests.contrib.claude_agent_sdk.utils import MOCK_STRUCTURED_OUTPUT
from tests.contrib.claude_agent_sdk.utils import MOCK_TOOL_ERROR_MESSAGE
from tests.contrib.claude_agent_sdk.utils import expected_agent_manifest
from tests.llmobs._utils import _expected_llmobs_llm_span_event
from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


CLAUDE_AGENT_SDK_VERSION = parse_version(claude_agent_sdk.__version__)

COMMON_TAGS = {"ml_app": "unnamed-ml-app", "service": "tests.llmobs", "integration": "claude_agent_sdk"}


class TestLLMObsClaudeAgentSdk:
    async def test_llmobs_query_extracts_content_and_usage(
        self, claude_agent_sdk, llmobs_events, mock_internal_client, test_spans
    ):
        prompt = "What is 2+2? Reply with just the number."
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        assert len(llmobs_events) == 3

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
                    {"content": "4", "role": "assistant"},
                    {"content": "4", "role": "assistant"},
                ]
            ),
            metadata={"stop_reason": "end_turn", "_dd": {"agent_manifest": expected_agent_manifest()}},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_query_with_options(self, claude_agent_sdk, llmobs_events, mock_internal_client, test_spans):
        prompt = "Test prompt with options"
        options = claude_agent_sdk.ClaudeAgentOptions(max_turns=3)

        async for _ in claude_agent_sdk.query(prompt=prompt, options=options):
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        assert len(llmobs_events) == 3

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
                    {"content": "4", "role": "assistant"},
                    {"content": "4", "role": "assistant"},
                ]
            ),
            metadata={
                "max_turns": 3,
                "stop_reason": "end_turn",
                "_dd": {"agent_manifest": expected_agent_manifest(max_iterations=3)},
            },
            token_metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_query_with_structured_output(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_structured_output, test_spans
    ):
        prompt = "What is 2+2? Return as JSON."
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        assert len(llmobs_events) == 3

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
                    {"content": "4", "role": "assistant"},
                    {"content": safe_json(MOCK_STRUCTURED_OUTPUT), "role": "assistant"},
                ]
            ),
            metadata={"stop_reason": "end_turn", "_dd": {"agent_manifest": expected_agent_manifest()}},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_query_error_no_output(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_error, test_spans
    ):
        prompt = "This will fail"

        with pytest.raises(ValueError):
            async for _ in claude_agent_sdk.query(prompt=prompt):
                pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        assert agent_span.error == 1
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        assert len(llmobs_events) == 3

        input_msgs = [{"content": prompt, "role": "user"}]

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name="",
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=[{"content": "", "role": ""}],
            error="builtins.ValueError",
            error_message="Connection failed",
            error_stack=ANY,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json([{"content": ""}]),
            error="builtins.ValueError",
            error_message="Connection failed",
            error_stack=ANY,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json([{"content": ""}]),
            metadata={"_dd": {"agent_manifest": {"framework": "Claude Agent SDK"}}},
            token_metrics={},
            tags=COMMON_TAGS,
            error="builtins.ValueError",
            error_message="Connection failed",
            error_stack=ANY,
        )

    async def test_llmobs_assistant_message_error_marks_llm_span_as_error(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_assistant_message_error, test_spans
    ):
        prompt = "Hello"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        assert len(llmobs_events) == 3

        input_msgs = [{"content": prompt, "role": "user"}]

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=[{"content": "", "role": ""}],
            error=MOCK_ASSISTANT_MESSAGE_ERROR,
            error_message=MOCK_ASSISTANT_MESSAGE_ERROR,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json([{"content": ""}]),
            error=MOCK_ASSISTANT_MESSAGE_ERROR,
            error_message=MOCK_ASSISTANT_MESSAGE_ERROR,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
                    {"content": "4", "role": "assistant"},
                ]
            ),
            metadata={"stop_reason": "end_turn", "_dd": {"agent_manifest": expected_agent_manifest()}},
            token_metrics=EXPECTED_QUERY_USAGE,
            error=MOCK_ASSISTANT_MESSAGE_ERROR,
            error_message=MOCK_ASSISTANT_MESSAGE_ERROR,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_client_query_captures_prompt(self, mock_client, llmobs_events, test_spans):
        prompt = "Hello from client!"

        await mock_client.query(prompt=prompt)
        async for _ in mock_client.receive_messages():
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        assert len(llmobs_events) == 3

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
                    {"content": "4", "role": "assistant"},
                ]
            ),
            metadata={
                **({"stop_reason": "end_turn"} if CLAUDE_AGENT_SDK_VERSION >= (0, 1, 49) else {}),
                "_dd": {"agent_manifest": expected_agent_manifest()},
            },
            token_metrics={
                "input_tokens": 14599,
                "output_tokens": 5,
                "total_tokens": 14604,
                "cache_write_input_tokens": 12742,
                "cache_read_input_tokens": 1854,
            },
            tags=COMMON_TAGS,
        )

    async def test_llmobs_query_with_read_tool_use(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_tool_use, test_spans
    ):
        prompt = "Read the file at /etc/hostname"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")
        tool_span = next(s for s in spans if "tool" in s.name)

        assert len(llmobs_events) == 4

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [
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

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            tool_span,
            span_kind="tool",
            input_value=safe_json({"file_path": "/etc/hostname"}),
            output_value="",
            metadata={"tool_id": MOCK_READ_TOOL_ID},
            tags=COMMON_TAGS,
        )
        assert llmobs_events[3] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
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
                    {"content": "4", "role": "assistant"},
                ]
            ),
            metadata={"stop_reason": "end_turn", "_dd": {"agent_manifest": expected_agent_manifest()}},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_query_with_bash_tool_use(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_bash_tool, test_spans
    ):
        prompt = "Run 'echo hello' using the Bash tool"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")
        tool_span = next(s for s in spans if "tool" in s.name)

        assert len(llmobs_events) == 4

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [
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

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            tool_span,
            span_kind="tool",
            input_value=safe_json(MOCK_BASH_TOOL_INPUT),
            output_value="",
            metadata={"tool_id": MOCK_BASH_TOOL_ID},
            tags=COMMON_TAGS,
        )
        assert llmobs_events[3] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
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
                    {"content": "4", "role": "assistant"},
                ]
            ),
            metadata={"stop_reason": "end_turn", "_dd": {"agent_manifest": expected_agent_manifest()}},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_query_with_grep_tool_use(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_grep_tool, test_spans
    ):
        prompt = "Use the Grep tool to search for 'def test_' in the tests directory"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")
        tool_span = next(s for s in spans if "tool" in s.name)

        assert len(llmobs_events) == 4

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [
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

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            tool_span,
            span_kind="tool",
            input_value=safe_json(MOCK_GREP_TOOL_INPUT),
            output_value="",
            metadata={"tool_id": MOCK_GREP_TOOL_ID},
            tags=COMMON_TAGS,
        )
        assert llmobs_events[3] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
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
                    {"content": "4", "role": "assistant"},
                ]
            ),
            metadata={"stop_reason": "end_turn", "_dd": {"agent_manifest": expected_agent_manifest()}},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_query_with_async_iterable_prompt(
        self, claude_agent_sdk, llmobs_events, mock_internal_client, test_spans
    ):
        async def prompt_generator():
            yield {"type": "user", "message": {"role": "user", "content": "Hello"}}
            yield {"type": "user", "message": {"role": "user", "content": "What is 2+2?"}}

        async for _ in claude_agent_sdk.query(prompt=prompt_generator()):
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        assert len(llmobs_events) == 3

        input_msgs = [{"content": "Hello", "role": "user"}, {"content": "What is 2+2?", "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
                    {"content": "4", "role": "assistant"},
                    {"content": "4", "role": "assistant"},
                ]
            ),
            metadata={"stop_reason": "end_turn", "_dd": {"agent_manifest": expected_agent_manifest()}},
            token_metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_client_query_with_async_iterable_prompt(self, mock_client, llmobs_events, test_spans):
        async def prompt_generator():
            yield {"type": "user", "message": {"role": "user", "content": "Hello"}}
            yield {"type": "user", "message": {"role": "user", "content": "What is 2+2?"}}

        await mock_client.query(prompt=prompt_generator())
        async for _ in mock_client.receive_messages():
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        assert len(llmobs_events) == 3

        input_msgs = [{"content": "Hello", "role": "user"}, {"content": "What is 2+2?", "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            agent_span,
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
                    {"content": "4", "role": "assistant"},
                ]
            ),
            metadata={
                **({"stop_reason": "end_turn"} if CLAUDE_AGENT_SDK_VERSION >= (0, 1, 49) else {}),
                "_dd": {"agent_manifest": expected_agent_manifest()},
            },
            token_metrics={
                "input_tokens": 14599,
                "output_tokens": 5,
                "total_tokens": 14604,
                "cache_write_input_tokens": 12742,
                "cache_read_input_tokens": 1854,
            },
            tags=COMMON_TAGS,
        )

    async def test_llmobs_multi_turn_produces_two_step_spans(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_tool_use_with_followup, test_spans
    ):
        """Multi-turn with a tool call produces two step spans, one per inference cycle.
        Step 1 contains an llm span and a tool span; step 2 contains only an llm span.
        """
        prompt = "Read /etc/hostname and tell me the hostname."
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = test_spans.pop_traces()[0]
        agent_span = spans[0]
        step_spans = [s for s in spans if s.name == "claude_agent_sdk.step"]
        llm_spans = [s for s in spans if s.name == "claude_agent_sdk.llm"]
        tool_span = next(s for s in spans if "tool" in s.name)

        # 2 step spans + 2 llm spans + 1 tool span + 1 agent span = 6 events
        assert len(llmobs_events) == 6
        assert len(step_spans) == 2
        assert len(llm_spans) == 2

        # Both step spans are children of the agent span
        for step_span in step_spans:
            assert step_span.parent_id == agent_span.span_id

        # Both llm spans are children of their respective step spans
        assert llm_spans[0].parent_id == step_spans[0].span_id
        assert llm_spans[1].parent_id == step_spans[1].span_id

        # Tool span is a child of step 1 (same level as the llm span)
        assert tool_span.parent_id == step_spans[0].span_id

        tags = COMMON_TAGS
        turn1_input = [{"content": prompt, "role": "user"}]
        turn1_output = [
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
        turn2_input = [
            {"content": prompt, "role": "user"},
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
            {
                "content": "",
                "role": "user",
                "tool_results": [{"result": "myhost.local", "tool_id": MOCK_READ_TOOL_ID, "type": "tool_result"}],
            },
        ]
        turn2_output = [{"content": MOCK_FINAL_ASSISTANT_TEXT, "role": "assistant"}]

        # [0] llm span for step 1
        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_spans[0],
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=turn1_input,
            output_messages=turn1_output,
            tags=tags,
        )

        # [1] tool span
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            tool_span,
            span_kind="tool",
            input_value=safe_json({"file_path": "/etc/hostname"}),
            output_value="myhost.local",
            metadata={"tool_id": MOCK_READ_TOOL_ID},
            tags=tags,
        )

        # [2] step span for step 1 (container: llm + tool)
        assert llmobs_events[2] == _expected_llmobs_non_llm_span_event(
            step_spans[0],
            span_kind="step",
            input_value=safe_json(turn1_input),
            output_value=safe_json(turn1_output),
            tags=tags,
        )

        # [3] llm span for step 2
        assert llmobs_events[3] == _expected_llmobs_llm_span_event(
            llm_spans[1],
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=turn2_input,
            output_messages=turn2_output,
            tags=tags,
        )

        # [4] step span for step 2
        assert llmobs_events[4] == _expected_llmobs_non_llm_span_event(
            step_spans[1],
            span_kind="step",
            input_value=safe_json(turn2_input),
            output_value=safe_json(turn2_output),
            tags=tags,
        )

        # [5] agent span
        assert llmobs_events[5]["meta"]["span"]["kind"] == "agent"

    async def test_llmobs_llm_span_includes_token_usage(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_with_usage, test_spans
    ):
        """LLM span should include token metrics when AssistantMessage has usage data."""
        prompt = "What is 2+2?"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = test_spans.pop_traces()[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        assert len(llmobs_events) == 3

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert llmobs_events[0] == _expected_llmobs_llm_span_event(
            llm_span,
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            token_metrics=EXPECTED_ASSISTANT_USAGE,
            tags=COMMON_TAGS,
        )
        assert llmobs_events[1] == _expected_llmobs_non_llm_span_event(
            step_span,
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            token_metrics=EXPECTED_ASSISTANT_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_tool_error_marks_tool_span_as_error(
        self, claude_agent_sdk, llmobs_events, mock_internal_client_tool_error, test_spans
    ):
        """ToolResultBlock with is_error=True should mark the tool span as an error."""
        prompt = "Read the file at /etc/hostname"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = test_spans.pop_traces()[0]
        tool_span = next(s for s in spans if "tool" in s.name)

        assert tool_span.error == 1

        tool_event = next(e for e in llmobs_events if e["meta"]["span"]["kind"] == "tool")
        assert tool_event == _expected_llmobs_non_llm_span_event(
            tool_span,
            span_kind="tool",
            input_value=safe_json({"file_path": "/etc/hostname"}),
            output_value=MOCK_TOOL_ERROR_MESSAGE,
            metadata={"tool_id": MOCK_READ_TOOL_ID},
            error="ToolError",
            error_message=MOCK_TOOL_ERROR_MESSAGE,
            tags=COMMON_TAGS,
        )
