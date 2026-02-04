"""Test utilities for claude_agent_sdk tests.

Since claude-agent-sdk uses subprocess/CLI transport (not HTTP), we mock
the internal transport layer and provide mock message responses.

Mock data is based on real SDK responses captured from sample app execution.
See: .analysis/claude-agent-sdk/sample_app/artifacts/captured-responses.json
"""

from claude_agent_sdk import AssistantMessage
from claude_agent_sdk import ResultMessage
from claude_agent_sdk import SystemMessage
from claude_agent_sdk import TextBlock
from claude_agent_sdk import ToolResultBlock
from claude_agent_sdk import ToolUseBlock
from claude_agent_sdk import UserMessage


# Real model name from captured SDK responses
MOCK_MODEL = "claude-sonnet-4-5-20250929"


def create_mock_system_message(
    session_id: str = "test-session-id",
    model: str = MOCK_MODEL,
) -> SystemMessage:
    """Create a mock SystemMessage for testing (init message with session info)."""
    return SystemMessage(
        subtype="init",
        data={
            "type": "system",
            "subtype": "init",
            "cwd": "/test/path",
            "session_id": session_id,
            "tools": ["Task", "Bash", "Read", "Write", "Grep"],
            "mcp_servers": [],
            "model": model,
            "permissionMode": "default",
            "apiKeySource": "ANTHROPIC_API_KEY",
            "claude_code_version": "2.0.62",
        },
    )


def create_mock_assistant_message(text: str, model: str = MOCK_MODEL) -> AssistantMessage:
    """Create a mock AssistantMessage for testing."""
    return AssistantMessage(
        content=[TextBlock(text=text)],
        model=model,
    )


def create_mock_assistant_message_with_tool_use(
    tool_name: str,
    tool_input: dict,
    tool_use_id: str = "tool_use_123",
    model: str = MOCK_MODEL,
) -> AssistantMessage:
    """Create a mock AssistantMessage with a tool use block.

    This simulates when Claude decides to use a tool.
    """
    return AssistantMessage(
        content=[ToolUseBlock(id=tool_use_id, name=tool_name, input=tool_input)],
        model=model,
    )


def create_mock_result_message(
    subtype: str = "success",
    duration_ms: int = 2021,
    duration_api_ms: int = 1925,
    is_error: bool = False,
    num_turns: int = 1,
    session_id: str = "test-session-id",
    total_cost_usd: float = 0.0484227,
    result: str = "4",
    usage: dict = None,
) -> ResultMessage:
    """Create a mock ResultMessage for testing with realistic usage data.

    Default values are based on real captured SDK responses.
    """
    if usage is None:
        # Real usage data from captured response
        usage = {
            "input_tokens": 3,
            "cache_creation_input_tokens": 12742,
            "cache_read_input_tokens": 1854,
            "output_tokens": 5,
            "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
            "service_tier": "standard",
        }
    return ResultMessage(
        subtype=subtype,
        duration_ms=duration_ms,
        duration_api_ms=duration_api_ms,
        is_error=is_error,
        num_turns=num_turns,
        session_id=session_id,
        total_cost_usd=total_cost_usd,
        usage=usage,
        result=result,
    )


def create_mock_user_message(content: str) -> UserMessage:
    """Create a mock UserMessage for testing."""
    return UserMessage(content=content)


MOCK_SYSTEM_MESSAGE = create_mock_system_message()
MOCK_ASSISTANT_RESPONSE = create_mock_assistant_message("4")
MOCK_RESULT_MESSAGE = create_mock_result_message()


MOCK_QUERY_RESPONSE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_ASSISTANT_RESPONSE,
    MOCK_RESULT_MESSAGE,
]


MOCK_READ_TOOL_ID = "toolu_01C4Thx957VoSn21zERxbeQX"
MOCK_TOOL_USE_ASSISTANT = create_mock_assistant_message_with_tool_use(
    tool_name="Read",
    tool_input={"file_path": "/etc/hostname"},
    tool_use_id=MOCK_READ_TOOL_ID,
)
MOCK_TOOL_USE_RESPONSE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_TOOL_USE_ASSISTANT,
    MOCK_RESULT_MESSAGE,
]

MOCK_BASH_TOOL_ID = "toolu_01D1aCzZ2rJhRNrmpXz9tRCd"
MOCK_BASH_TOOL_INPUT = {
    "command": "echo hello",
    "description": "Print 'hello' to standard output",
}
MOCK_BASH_TOOL_ASSISTANT = create_mock_assistant_message_with_tool_use(
    tool_name="Bash",
    tool_input=MOCK_BASH_TOOL_INPUT,
    tool_use_id=MOCK_BASH_TOOL_ID,
)
MOCK_BASH_TOOL_RESPONSE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_BASH_TOOL_ASSISTANT,
    MOCK_RESULT_MESSAGE,
]

MOCK_GREP_TOOL_ID = "toolu_01C8pRGXaxzFMm28FSg7Zeda"
MOCK_GREP_TOOL_INPUT = {
    "pattern": "def test_",
    "path": "tests",
    "output_mode": "content",
    "head_limit": 3,
}
MOCK_GREP_TOOL_ASSISTANT = create_mock_assistant_message_with_tool_use(
    tool_name="Grep",
    tool_input=MOCK_GREP_TOOL_INPUT,
    tool_use_id=MOCK_GREP_TOOL_ID,
)
MOCK_GREP_TOOL_RESPONSE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_GREP_TOOL_ASSISTANT,
    MOCK_RESULT_MESSAGE,
]

EXPECTED_INPUT_TOKENS = 3 + 12742 + 1854  # input + cache_creation + cache_read = 14599
EXPECTED_OUTPUT_TOKENS = 5
EXPECTED_TOTAL_TOKENS = EXPECTED_INPUT_TOKENS + EXPECTED_OUTPUT_TOKENS


MOCK_CLIENT_RAW_MESSAGES = [
    {
        "type": "system",
        "subtype": "init",
        "cwd": "/test/path",
        "session_id": "test-session-id",
        "tools": ["Task", "Bash", "Read"],
        "model": MOCK_MODEL,
    },
    {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "4"}], "model": MOCK_MODEL},
    },
    {
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
    },
]
