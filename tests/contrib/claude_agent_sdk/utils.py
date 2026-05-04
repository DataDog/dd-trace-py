"""Test utilities for claude_agent_sdk tests.

Since claude-agent-sdk uses subprocess/CLI transport (not HTTP), we mock
the internal transport layer and provide mock message responses.
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


EXPECTED_SYSTEM_MESSAGE_DATA = {
    "type": "system",
    "subtype": "init",
    "cwd": "/test/path",
    "session_id": "test-session-id",
    "tools": ["Task", "Bash", "Read", "Write", "Grep"],
    "mcp_servers": [],
    "model": MOCK_MODEL,
    "permissionMode": "default",
    "apiKeySource": "ANTHROPIC_API_KEY",
    "claude_code_version": "2.0.62",
}


def expected_agent_manifest(max_iterations=None):
    """Helper to build expected agent manifest."""
    manifest = {
        "framework": "Claude Agent SDK",
        "model": MOCK_MODEL,
        "tools": [
            {"name": "Task"},
            {"name": "Bash"},
            {"name": "Read"},
            {"name": "Write"},
            {"name": "Grep"},
        ],
        "dependencies": {"mcp_servers": []},
    }
    if max_iterations is not None:
        manifest["max_iterations"] = max_iterations
    return manifest


def create_mock_system_message(
    session_id: str = "test-session-id",
    model: str = MOCK_MODEL,
) -> SystemMessage:
    """Create a mock SystemMessage for testing (init message with session info)."""
    return SystemMessage(
        subtype="init",
        data=EXPECTED_SYSTEM_MESSAGE_DATA,
    )


MOCK_ASSISTANT_USAGE = {
    "input_tokens": 10,
    "output_tokens": 3,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
}

EXPECTED_ASSISTANT_USAGE = {
    "input_tokens": 10,
    "output_tokens": 3,
    "total_tokens": 13,
}


def create_mock_assistant_message(text: str, model: str = MOCK_MODEL, usage: dict = None) -> AssistantMessage:
    """Create a mock AssistantMessage for testing."""
    msg = AssistantMessage(
        content=[TextBlock(text=text)],
        model=model,
    )
    if usage is not None:
        msg.usage = usage
    return msg


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
    stop_reason: str = "end_turn",
    structured_output: object = None,
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
    msg = ResultMessage(
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
    # stop_reason and structured_output fields were added in newer claude-agent-sdk versions;
    # set via setattr for compatibility with older SDK versions that lack these fields
    msg.stop_reason = stop_reason
    msg.structured_output = structured_output
    return msg


def create_mock_user_message(content: str) -> UserMessage:
    """Create a mock UserMessage for testing."""
    return UserMessage(content=content)


MOCK_SYSTEM_MESSAGE = create_mock_system_message()
MOCK_ASSISTANT_RESPONSE = create_mock_assistant_message("4")
MOCK_ASSISTANT_RESPONSE_WITH_USAGE = create_mock_assistant_message("4", usage=MOCK_ASSISTANT_USAGE)
MOCK_RESULT_MESSAGE = create_mock_result_message()


MOCK_QUERY_RESPONSE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_ASSISTANT_RESPONSE,
    MOCK_RESULT_MESSAGE,
]

MOCK_ASSISTANT_MESSAGE_ERROR = "invalid_request"
MOCK_ASSISTANT_MESSAGE_WITH_ERROR = AssistantMessage(content=[], model=MOCK_MODEL)
MOCK_ASSISTANT_MESSAGE_WITH_ERROR.error = MOCK_ASSISTANT_MESSAGE_ERROR
MOCK_ASSISTANT_MESSAGE_ERROR_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_ASSISTANT_MESSAGE_WITH_ERROR,
    MOCK_RESULT_MESSAGE,
]

MOCK_QUERY_RESPONSE_SEQUENCE_WITH_USAGE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_ASSISTANT_RESPONSE_WITH_USAGE,
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

MOCK_TOOL_RESULT_USER_READ = UserMessage(
    content=[ToolResultBlock(tool_use_id=MOCK_READ_TOOL_ID, content="myhost.local")]
)
MOCK_FINAL_ASSISTANT_TEXT = "The hostname is myhost.local"
MOCK_FINAL_ASSISTANT = create_mock_assistant_message(MOCK_FINAL_ASSISTANT_TEXT)
MOCK_MULTI_TURN_RESULT_MESSAGE = create_mock_result_message(result=MOCK_FINAL_ASSISTANT_TEXT)

MOCK_TOOL_USE_WITH_FOLLOWUP_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_TOOL_USE_ASSISTANT,  # AssistantMessage with ToolUseBlock → LLM span #1 + tool span
    MOCK_TOOL_RESULT_USER_READ,  # UserMessage with ToolResultBlock → finishes tool span
    MOCK_FINAL_ASSISTANT,  # AssistantMessage with text → LLM span #2
    MOCK_MULTI_TURN_RESULT_MESSAGE,
]

MOCK_TOOL_ERROR_MESSAGE = "Permission denied: /etc/hostname"
MOCK_TOOL_ERROR_USER_READ = UserMessage(
    content=[ToolResultBlock(tool_use_id=MOCK_READ_TOOL_ID, content=MOCK_TOOL_ERROR_MESSAGE, is_error=True)]
)
MOCK_TOOL_ERROR_RESPONSE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_TOOL_USE_ASSISTANT,
    MOCK_TOOL_ERROR_USER_READ,
    MOCK_FINAL_ASSISTANT,
    MOCK_MULTI_TURN_RESULT_MESSAGE,
]


MOCK_STRUCTURED_OUTPUT = {"answer": 4, "unit": "integer"}
MOCK_STRUCTURED_RESULT_MESSAGE = create_mock_result_message(
    result=None,
    structured_output=MOCK_STRUCTURED_OUTPUT,
)
MOCK_STRUCTURED_OUTPUT_RESPONSE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_ASSISTANT_RESPONSE,
    MOCK_STRUCTURED_RESULT_MESSAGE,
]

EXPECTED_CACHE_WRITE_INPUT_TOKENS = 12742
EXPECTED_CACHE_READ_INPUT_TOKENS = 1854
EXPECTED_INPUT_TOKENS = 3 + EXPECTED_CACHE_WRITE_INPUT_TOKENS + EXPECTED_CACHE_READ_INPUT_TOKENS
EXPECTED_OUTPUT_TOKENS = 5
EXPECTED_TOTAL_TOKENS = EXPECTED_INPUT_TOKENS + EXPECTED_OUTPUT_TOKENS
EXPECTED_QUERY_USAGE = {
    "input_tokens": EXPECTED_INPUT_TOKENS,
    "output_tokens": EXPECTED_OUTPUT_TOKENS,
    "total_tokens": EXPECTED_TOTAL_TOKENS,
    "cache_write_input_tokens": EXPECTED_CACHE_WRITE_INPUT_TOKENS,
    "cache_read_input_tokens": EXPECTED_CACHE_READ_INPUT_TOKENS,
}


# mocked client messages are in a raw format compared to normal query responses
MOCK_CLIENT_RAW_MESSAGES = [
    EXPECTED_SYSTEM_MESSAGE_DATA,
    {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "4"}], "model": MOCK_MODEL},
    },
    {
        "type": "result",
        "subtype": "success",
        "stop_reason": "end_turn",
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
