from unittest.mock import ANY

import claude_agent_sdk
import pytest

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_span_links
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
from tests.contrib.claude_agent_sdk.utils import MOCK_PARALLEL_BASH_TOOL_IDS
from tests.contrib.claude_agent_sdk.utils import MOCK_READ_TOOL_ID
from tests.contrib.claude_agent_sdk.utils import MOCK_STRUCTURED_OUTPUT
from tests.contrib.claude_agent_sdk.utils import MOCK_TOOL_ERROR_MESSAGE
from tests.contrib.claude_agent_sdk.utils import expected_agent_manifest
from tests.llmobs._utils import _assert_span_link
from tests.llmobs._utils import assert_llmobs_span_data


CLAUDE_AGENT_SDK_VERSION = parse_version(claude_agent_sdk.__version__)

COMMON_TAGS = {"ml_app": "unnamed-ml-app", "service": "tests.llmobs", "integration": "claude_agent_sdk"}


class TestLLMObsClaudeAgentSdk:
    async def test_llmobs_query_extracts_content_and_usage(
        self, claude_agent_sdk, mock_internal_client, claude_agent_sdk_llmobs, test_spans
    ):
        prompt = "What is 2+2? Reply with just the number."
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 3
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
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
            metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )
        _assert_span_link(llm_span, agent_span, "output", "output")
        _assert_span_link(agent_span, step_span, "input", "input")
        _assert_span_link(step_span, agent_span, "output", "output")

        # Nicole's tweak: leaf chain enters via step-level (agent → step₁),
        # so the first llm has NO incoming leaf-level link. A regression that
        # adds the redundant agent → llm₁ link would fire here.
        llm1_links = get_llmobs_span_links(llm_span) or []
        assert not any(link["span_id"] == str(agent_span.span_id) for link in llm1_links), (
            "Unexpected agent → llm₁ link — Nicole's no-entry-leaf-link design"
        )

    async def test_llmobs_query_with_options(
        self, claude_agent_sdk, mock_internal_client, claude_agent_sdk_llmobs, test_spans
    ):
        prompt = "Test prompt with options"
        options = claude_agent_sdk.ClaudeAgentOptions(max_turns=3)

        async for _ in claude_agent_sdk.query(prompt=prompt, options=options):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 3
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
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
            metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_query_with_structured_output(
        self, claude_agent_sdk, mock_internal_client_structured_output, claude_agent_sdk_llmobs, test_spans
    ):
        prompt = "What is 2+2? Return as JSON."
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 3
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
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
            metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_query_error_no_output(
        self, claude_agent_sdk, mock_internal_client_error, claude_agent_sdk_llmobs, test_spans
    ):
        prompt = "This will fail"

        with pytest.raises(ValueError):
            async for _ in claude_agent_sdk.query(prompt=prompt):
                pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 3
        agent_span = spans[0]
        assert agent_span.error == 1
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        input_msgs = [{"content": prompt, "role": "user"}]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name="unknown",
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=[{"content": "", "role": ""}],
            error={"type": "builtins.ValueError", "message": "Connection failed", "stack": ANY},
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json([{"content": ""}]),
            error={"type": "builtins.ValueError", "message": "Connection failed", "stack": ANY},
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json([{"content": ""}]),
            metadata={"_dd": {"agent_manifest": {"framework": "Claude Agent SDK"}}},
            metrics={},
            tags=COMMON_TAGS,
            error={"type": "builtins.ValueError", "message": "Connection failed", "stack": ANY},
        )
        _assert_span_link(llm_span, agent_span, "output", "output")
        _assert_span_link(agent_span, step_span, "input", "input")
        _assert_span_link(step_span, agent_span, "output", "output")

    async def test_llmobs_assistant_message_error_marks_llm_span_as_error(
        self, claude_agent_sdk, mock_internal_client_assistant_message_error, claude_agent_sdk_llmobs, test_spans
    ):
        prompt = "Hello"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 3
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        input_msgs = [{"content": prompt, "role": "user"}]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=[{"content": "", "role": ""}],
            error={"type": MOCK_ASSISTANT_MESSAGE_ERROR, "message": MOCK_ASSISTANT_MESSAGE_ERROR, "stack": ANY},
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json([{"content": ""}]),
            error={"type": MOCK_ASSISTANT_MESSAGE_ERROR, "message": MOCK_ASSISTANT_MESSAGE_ERROR, "stack": ANY},
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
            span_kind="agent",
            input_value=safe_json(input_msgs),
            output_value=safe_json(
                [
                    {"content": safe_json(EXPECTED_SYSTEM_MESSAGE_DATA), "role": "system"},
                    {"content": "4", "role": "assistant"},
                ]
            ),
            metadata={"stop_reason": "end_turn", "_dd": {"agent_manifest": expected_agent_manifest()}},
            metrics=EXPECTED_QUERY_USAGE,
            error={"type": MOCK_ASSISTANT_MESSAGE_ERROR, "message": MOCK_ASSISTANT_MESSAGE_ERROR, "stack": ANY},
            tags=COMMON_TAGS,
        )
        _assert_span_link(llm_span, agent_span, "output", "output")
        _assert_span_link(agent_span, step_span, "input", "input")
        _assert_span_link(step_span, agent_span, "output", "output")

    async def test_llmobs_client_query_captures_prompt(self, mock_client, claude_agent_sdk_llmobs, test_spans):
        prompt = "Hello from client!"

        await mock_client.query(prompt=prompt)
        async for _ in mock_client.receive_messages():
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 3
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
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
            metrics={
                "input_tokens": 14599,
                "output_tokens": 5,
                "total_tokens": 14604,
                "cache_write_input_tokens": 12742,
                "cache_read_input_tokens": 1854,
            },
            tags=COMMON_TAGS,
        )

    async def test_llmobs_query_with_read_tool_use(
        self, claude_agent_sdk, mock_internal_client_tool_use, claude_agent_sdk_llmobs, test_spans
    ):
        prompt = "Read the file at /etc/hostname"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 4
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")
        tool_span = next(s for s in spans if "tool" in s.name)

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

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(tool_span),
            span_kind="tool",
            input_value=safe_json({"file_path": "/etc/hostname"}),
            output_value="",
            metadata={"tool_id": MOCK_READ_TOOL_ID},
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
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
            metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )
        _assert_span_link(llm_span, tool_span, "output", "input")
        _assert_span_link(llm_span, agent_span, "output", "output")
        _assert_span_link(agent_span, step_span, "input", "input")
        _assert_span_link(step_span, agent_span, "output", "output")

    async def test_llmobs_query_with_bash_tool_use(
        self, claude_agent_sdk, mock_internal_client_bash_tool, claude_agent_sdk_llmobs, test_spans
    ):
        prompt = "Run 'echo hello' using the Bash tool"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 4
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")
        tool_span = next(s for s in spans if "tool" in s.name)

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

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(tool_span),
            span_kind="tool",
            input_value=safe_json(MOCK_BASH_TOOL_INPUT),
            output_value="",
            metadata={"tool_id": MOCK_BASH_TOOL_ID},
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
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
            metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )
        _assert_span_link(llm_span, tool_span, "output", "input")
        _assert_span_link(llm_span, agent_span, "output", "output")
        _assert_span_link(agent_span, step_span, "input", "input")
        _assert_span_link(step_span, agent_span, "output", "output")

    async def test_llmobs_query_with_grep_tool_use(
        self, claude_agent_sdk, mock_internal_client_grep_tool, claude_agent_sdk_llmobs, test_spans
    ):
        prompt = "Use the Grep tool to search for 'def test_' in the tests directory"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 4
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")
        tool_span = next(s for s in spans if "tool" in s.name)

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

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(tool_span),
            span_kind="tool",
            input_value=safe_json(MOCK_GREP_TOOL_INPUT),
            output_value="",
            metadata={"tool_id": MOCK_GREP_TOOL_ID},
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
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
            metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )
        _assert_span_link(llm_span, tool_span, "output", "input")
        _assert_span_link(llm_span, agent_span, "output", "output")
        _assert_span_link(agent_span, step_span, "input", "input")
        _assert_span_link(step_span, agent_span, "output", "output")

    async def test_llmobs_query_with_async_iterable_prompt(
        self, claude_agent_sdk, mock_internal_client, claude_agent_sdk_llmobs, test_spans
    ):
        async def prompt_generator():
            yield {"type": "user", "message": {"role": "user", "content": "Hello"}}
            yield {"type": "user", "message": {"role": "user", "content": "What is 2+2?"}}

        async for _ in claude_agent_sdk.query(prompt=prompt_generator()):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 3
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        input_msgs = [{"content": "Hello", "role": "user"}, {"content": "What is 2+2?", "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
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
            metrics=EXPECTED_QUERY_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_client_query_with_async_iterable_prompt(
        self, mock_client, claude_agent_sdk_llmobs, test_spans
    ):
        async def prompt_generator():
            yield {"type": "user", "message": {"role": "user", "content": "Hello"}}
            yield {"type": "user", "message": {"role": "user", "content": "What is 2+2?"}}

        await mock_client.query(prompt=prompt_generator())
        async for _ in mock_client.receive_messages():
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 3
        agent_span = spans[0]
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        input_msgs = [{"content": "Hello", "role": "user"}, {"content": "What is 2+2?", "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(agent_span),
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
            metrics={
                "input_tokens": 14599,
                "output_tokens": 5,
                "total_tokens": 14604,
                "cache_write_input_tokens": 12742,
                "cache_read_input_tokens": 1854,
            },
            tags=COMMON_TAGS,
        )

    async def test_llmobs_multi_turn_produces_two_step_spans(
        self, claude_agent_sdk, mock_internal_client_tool_use_with_followup, claude_agent_sdk_llmobs, test_spans
    ):
        """Multi-turn with a tool call produces two step spans, one per inference cycle.
        Step 1 contains an llm span and a tool span; step 2 contains only an llm span.
        """
        prompt = "Read /etc/hostname and tell me the hostname."
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        # 2 step spans + 2 llm spans + 1 tool span + 1 agent span = 6 spans
        assert len(spans) == 6
        agent_span = spans[0]
        step_spans = [s for s in spans if s.name == "claude_agent_sdk.step"]
        llm_spans = [s for s in spans if s.name == "claude_agent_sdk.llm"]
        tool_span = next(s for s in spans if "tool" in s.name)

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

        # llm span for step 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_spans[0]),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=turn1_input,
            output_messages=turn1_output,
            tags=tags,
        )

        # tool span
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(tool_span),
            span_kind="tool",
            input_value=safe_json({"file_path": "/etc/hostname"}),
            output_value="myhost.local",
            metadata={"tool_id": MOCK_READ_TOOL_ID},
            tags=tags,
        )

        # step span for step 1 (container: llm + tool)
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_spans[0]),
            span_kind="step",
            input_value=safe_json(turn1_input),
            output_value=safe_json(turn1_output),
            tags=tags,
        )

        # llm span for step 2
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_spans[1]),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=turn2_input,
            output_messages=turn2_output,
            tags=tags,
        )

        # step span for step 2
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_spans[1]),
            span_kind="step",
            input_value=safe_json(turn2_input),
            output_value=safe_json(turn2_output),
            tags=tags,
        )

        # agent span
        assert_llmobs_span_data(_get_llmobs_data_metastruct(agent_span), span_kind="agent")

        _assert_span_link(llm_spans[0], tool_span, "output", "input")
        _assert_span_link(tool_span, llm_spans[1], "output", "input")
        _assert_span_link(llm_spans[1], agent_span, "output", "output")
        _assert_span_link(agent_span, step_spans[0], "input", "input")
        _assert_span_link(step_spans[0], step_spans[1], "output", "input")
        _assert_span_link(step_spans[1], agent_span, "output", "output")

    async def test_llmobs_llm_span_includes_token_usage(
        self, claude_agent_sdk, mock_internal_client_with_usage, claude_agent_sdk_llmobs, test_spans
    ):
        """LLM span should include token metrics when AssistantMessage has usage data."""
        prompt = "What is 2+2?"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 3
        step_span = next(s for s in spans if s.name == "claude_agent_sdk.step")
        llm_span = next(s for s in spans if s.name == "claude_agent_sdk.llm")

        input_msgs = [{"content": prompt, "role": "user"}]
        output_msgs = [{"content": "4", "role": "assistant"}]

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(llm_span),
            span_kind="llm",
            model_name=MOCK_MODEL,
            model_provider="anthropic",
            input_messages=input_msgs,
            output_messages=output_msgs,
            metrics=EXPECTED_ASSISTANT_USAGE,
            tags=COMMON_TAGS,
        )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(step_span),
            span_kind="step",
            input_value=safe_json(input_msgs),
            output_value=safe_json(output_msgs),
            metrics=EXPECTED_ASSISTANT_USAGE,
            tags=COMMON_TAGS,
        )

    async def test_llmobs_tool_error_marks_tool_span_as_error(
        self, claude_agent_sdk, mock_internal_client_tool_error, claude_agent_sdk_llmobs, test_spans
    ):
        """ToolResultBlock with is_error=True should mark the tool span as an error."""
        prompt = "Read the file at /etc/hostname"
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        agent_span = spans[0]
        llm_spans = [s for s in spans if s.name == "claude_agent_sdk.llm"]
        step_spans = [s for s in spans if s.name == "claude_agent_sdk.step"]
        tool_span = next(s for s in spans if "tool" in s.name)

        assert tool_span.error == 1

        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(tool_span),
            span_kind="tool",
            input_value=safe_json({"file_path": "/etc/hostname"}),
            output_value=MOCK_TOOL_ERROR_MESSAGE,
            metadata={"tool_id": MOCK_READ_TOOL_ID},
            error={"type": "ToolError", "message": MOCK_TOOL_ERROR_MESSAGE, "stack": ANY},
            tags=COMMON_TAGS,
        )
        assert len(step_spans) == 2
        _assert_span_link(llm_spans[0], tool_span, "output", "input")
        _assert_span_link(tool_span, llm_spans[-1], "output", "input")
        _assert_span_link(llm_spans[-1], agent_span, "output", "output")
        _assert_span_link(agent_span, step_spans[0], "input", "input")
        _assert_span_link(step_spans[0], step_spans[1], "output", "input")
        _assert_span_link(step_spans[-1], agent_span, "output", "output")

    async def test_llmobs_parallel_tool_use_links_all_tools_to_assistant(
        self,
        claude_agent_sdk,
        mock_internal_client_parallel_tool_use,
        claude_agent_sdk_llmobs,
        test_spans,
    ):
        """Parallel tool calls in a single AssistantMessage produce sibling tool spans.

        The dual-layer linking design fans out: every parallel tool span gets a
        link from the same llm span (``llm#1 → tool#k``), every parallel tool
        span links to the next llm span (``tool#k → llm#2``), and no tool span
        links to any other tool span (parallel tools are siblings, not a chain).
        """
        prompt = "Run three Bash commands in parallel."
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        agent_span = spans[0]
        # Sort by start time so step#1 is unambiguously the first step span.
        llm_spans = sorted([s for s in spans if s.name == "claude_agent_sdk.llm"], key=lambda s: s.start_ns)
        step_spans = sorted([s for s in spans if s.name == "claude_agent_sdk.step"], key=lambda s: s.start_ns)
        tool_spans = [s for s in spans if "tool" in s.name]

        # Sequence is: assistant(3 parallel ToolUseBlocks) → user(3 ToolResultBlocks)
        # → final assistant(text) → result. Expect 2 step spans, 2 llm spans, 3 tool spans.
        assert len(llm_spans) == 2
        assert len(step_spans) == 2
        assert len(tool_spans) == 3
        assert {s.name for s in tool_spans} == {"claude_agent_sdk.tool.Bash"}

        # AIDEV-NOTE: This test asserts span_link topology, NOT span tree shape.
        # The current ClaudeAgentSdkAsyncStreamHandler opens each ToolUseBlock's
        # span via integration.trace() without explicitly setting a parent, so
        # the tracer makes each parallel tool a child of the previously-opened
        # tool (instead of a sibling under the step). That is a pre-existing
        # tracer-context quirk independent of this PR's dual-layer linking
        # design — the span_links emitted are correct regardless of the
        # parent_id. Asserting parent_id == step would over-constrain the
        # test and fail on unrelated tree-shape changes.
        # Confirm every tool span IS at least a descendant of step#1 (not under
        # step#2 or directly under the agent).
        descendant_of_step1 = {step_spans[0].span_id}
        # BFS by parent_id over all spans to find step#1's full subtree.
        changed = True
        while changed:
            changed = False
            for s in spans:
                if s.parent_id in descendant_of_step1 and s.span_id not in descendant_of_step1:
                    descendant_of_step1.add(s.span_id)
                    changed = True
        for tool_span in tool_spans:
            assert tool_span.span_id in descendant_of_step1, (
                f"tool span {tool_span.span_id} is not in step#1's subtree "
                f"(step#1={step_spans[0].span_id}, step#2={step_spans[1].span_id})"
            )

        # Leaf-level fan-out: each tool gets one incoming link from llm#1.
        for tool_span in tool_spans:
            _assert_span_link(llm_spans[0], tool_span, "output", "input")

        # Leaf-level fan-in: llm#2 has one incoming link from each parallel tool.
        for tool_span in tool_spans:
            _assert_span_link(tool_span, llm_spans[1], "output", "input")

        # No tool→tool link: parallel tools are siblings, not chained. If the
        # production code ever started chaining them, this assertion would fire
        # (the for-loop body never raises because get_llmobs_span_links can
        # return None or an empty list).
        for tool_span in tool_spans:
            for link in get_llmobs_span_links(tool_span) or []:
                assert link["span_id"] not in {str(s.span_id) for s in tool_spans}, (
                    f"Tool span {tool_span.span_id} unexpectedly links to another tool span "
                    f"({link['span_id']}); parallel tools should be siblings, not chained."
                )

        # Step-level chain is unchanged by the parallel tool fan-out.
        _assert_span_link(agent_span, step_spans[0], "input", "input")
        _assert_span_link(step_spans[0], step_spans[1], "output", "input")
        _assert_span_link(step_spans[1], agent_span, "output", "output")
        _assert_span_link(llm_spans[1], agent_span, "output", "output")

        # Sanity: confirm the three expected tool_use_ids made it through to
        # the tool spans' metadata, so we know we actually exercised the
        # parallel fixture and not a single-tool fallback.
        tool_metadata_ids = set()
        for tool_span in tool_spans:
            metadata = _get_llmobs_data_metastruct(tool_span).get("meta", {}).get("metadata", {})
            tool_metadata_ids.add(metadata.get("tool_id"))
        assert tool_metadata_ids == set(MOCK_PARALLEL_BASH_TOOL_IDS)

    async def test_llmobs_back_to_back_assistant_messages_link_llm_to_llm_directly(
        self,
        claude_agent_sdk,
        mock_internal_client_double_assistant_no_tools,
        claude_agent_sdk_llmobs,
        test_spans,
    ):
        """Two text-only AssistantMessages back-to-back keep the leaf chain connected.

        Normal multi-turn flow has a tool span between consecutive llm spans, so the
        leaf chain is ``llm#1 → tool → llm#2``. If a step has no tools (e.g. two
        AssistantMessages with only text), there's nothing to link through, so the
        integration links ``llm#1 → llm#2`` directly to preserve the leaf chain.
        """
        prompt = "Test back-to-back assistant messages."
        async for _ in claude_agent_sdk.query(prompt=prompt):
            pass

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        agent_span = spans[0]
        llm_spans = [s for s in spans if s.name == "claude_agent_sdk.llm"]
        step_spans = [s for s in spans if s.name == "claude_agent_sdk.step"]

        assert len(llm_spans) == 2
        assert len(step_spans) == 2

        # Direct leaf-level link bridges the no-tool gap.
        _assert_span_link(llm_spans[0], llm_spans[1], "output", "input")
        # Step-level chain stays intact in parallel.
        _assert_span_link(agent_span, step_spans[0], "input", "input")
        _assert_span_link(step_spans[0], step_spans[1], "output", "input")
        _assert_span_link(step_spans[1], agent_span, "output", "output")
        _assert_span_link(llm_spans[1], agent_span, "output", "output")

    async def test_llmobs_client_sequential_queries(self, mock_client, claude_agent_sdk_llmobs, test_spans):
        """Two sequential ClaudeSDKClient.query() + receive_response() calls produce two
        separate root agent spans. Regression test for spans nesting under a prior
        unfinished agent span when receive_response() early-returns after ResultMessage.
        """
        for prompt in ("Q1", "Q2"):
            await mock_client.query(prompt=prompt)
            async for _ in mock_client.receive_response():
                pass

        traces = test_spans.pop_traces()
        assert len(traces) == 2
        agent_spans = [trace[0] for trace in traces]
        assert all(s.name == "claude_agent_sdk.ClaudeSDKClient.query" for s in agent_spans)
        assert all(s.parent_id is None for s in agent_spans)
        assert agent_spans[0].trace_id != agent_spans[1].trace_id


def test_shadow_tags_llm_with_cache_tokens(tracer):
    """Verify cache-token shadow metrics propagate from claude_agent_sdk usage to APM span."""
    from unittest.mock import MagicMock

    from ddtrace.llmobs._integrations.claude_agent_sdk import ClaudeAgentSdkIntegration

    integration = ClaudeAgentSdkIntegration(MagicMock())

    response = MagicMock()
    response.model = "claude-sonnet-4-5-20250929"
    response.usage = {
        "input_tokens": 12,
        "output_tokens": 8,
        "cache_creation_input_tokens": 5,
        "cache_read_input_tokens": 3,
    }

    with tracer.trace("claude_agent_sdk.request") as span:
        integration._set_apm_shadow_tags(span, [], {}, response=response, operation="llm")

    assert span.get_tag("_dd.llmobs.span_kind") == "llm"
    assert span.get_tag("_dd.llmobs.model_name") == "claude-sonnet-4-5-20250929"
    assert span.get_tag("_dd.llmobs.model_provider") == "anthropic"
    assert span.get_metric("_dd.llmobs.enabled") == 0
    # input_tokens is the sum of base + cache_creation + cache_read in this integration
    assert span.get_metric("_dd.llmobs.input_tokens") == 12 + 5 + 3
    assert span.get_metric("_dd.llmobs.output_tokens") == 8
    assert span.get_metric("_dd.llmobs.cache_read_input_tokens") == 3
    assert span.get_metric("_dd.llmobs.cache_write_input_tokens") == 5
