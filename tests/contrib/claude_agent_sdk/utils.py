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


# Standard mock responses for tests - based on "What is 2+2?" captured response
MOCK_SYSTEM_MESSAGE = create_mock_system_message()
MOCK_ASSISTANT_RESPONSE = create_mock_assistant_message("4")
MOCK_RESULT_MESSAGE = create_mock_result_message()

# Complete mock response sequence for standalone query() calls
# Matches real SDK behavior: SystemMessage -> AssistantMessage -> ResultMessage
MOCK_QUERY_RESPONSE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_ASSISTANT_RESPONSE,
    MOCK_RESULT_MESSAGE,
]

# Mock response with tool use - simulates Claude using a tool
# Real captured data from SDK shows:
# - Tool ID format: "toolu_01..." (26 chars total)
# - Read tool input: {"file_path": "..."}
# Source: .analysis/claude-agent-sdk/sample_app/artifacts/captured-tool-use.json
MOCK_READ_TOOL_ID = "toolu_01C4Thx957VoSn21zERxbeQX"  # Real captured ID format
MOCK_TOOL_USE_ASSISTANT = create_mock_assistant_message_with_tool_use(
    tool_name="Read",
    tool_input={"file_path": "/etc/hostname"},  # Real captured input
    tool_use_id=MOCK_READ_TOOL_ID,
)
MOCK_TOOL_USE_RESPONSE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_TOOL_USE_ASSISTANT,
    MOCK_RESULT_MESSAGE,
]

# Bash tool use - Real captured data
# Source: .analysis/claude-agent-sdk/sample_app/artifacts/captured-bash-grep.json
# Bash tool input: {"command": "...", "description": "..."}
MOCK_BASH_TOOL_ID = "toolu_01D1aCzZ2rJhRNrmpXz9tRCd"  # Real captured ID
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

# Grep tool use - Real captured data
# Source: .analysis/claude-agent-sdk/sample_app/artifacts/captured-bash-grep.json
# Grep tool input: {"pattern": "...", "path": "...", "output_mode": "...", "head_limit": N}
MOCK_GREP_TOOL_ID = "toolu_01C8pRGXaxzFMm28FSg7Zeda"  # Real captured ID
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

# Multi-turn conversation mock - Real captured data from:
# .analysis/claude-agent-sdk/sample_app/artifacts/captured-multi-turn.json
#
# IMPORTANT: Multi-turn in claude-agent-sdk means Claude used tools internally
# within a single query() call. The message flow is:
#   1. SystemMessage (init)
#   2. AssistantMessage (text explaining what it will do)
#   3. AssistantMessage (ToolUseBlock - invoking a tool)
#   4. UserMessage (ToolResultBlock - tool result)
#   5. AssistantMessage (final response text)
#   6. ResultMessage (num_turns=2, aggregated usage)
#
# Real captured data:
# - input_tokens: 10
# - cache_creation_input_tokens: 443
# - cache_read_input_tokens: 28863
# - output_tokens: 147
MOCK_MULTI_TURN_TOOL_ID = "toolu_01HjzS3PVXhgyuVARU9wAuKD"  # Real captured ID
MOCK_MULTI_TURN_USAGE = {
    "input_tokens": 10,
    "cache_creation_input_tokens": 443,
    "cache_read_input_tokens": 28863,
    "output_tokens": 147,
    "server_tool_use": {"web_search_requests": 0, "web_fetch_requests": 0},
    "service_tier": "standard",
}
MOCK_MULTI_TURN_RESULT = create_mock_result_message(
    subtype="success",
    num_turns=2,
    usage=MOCK_MULTI_TURN_USAGE,
    result="The file does not exist.",
    session_id="2cce0da9-cd86-44d2-8bb1-84cdf9ac44fd",
)


# Create UserMessage with ToolResultBlock for the tool result
def create_mock_user_message_with_tool_result(
    tool_use_id: str,
    content: str,
    is_error: bool = False,
) -> UserMessage:
    """Create a UserMessage containing a ToolResultBlock."""
    return UserMessage(content=[ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)])


MOCK_MULTI_TURN_RESPONSE_SEQUENCE = [
    # 1. SystemMessage - init
    create_mock_system_message(session_id="2cce0da9-cd86-44d2-8bb1-84cdf9ac44fd"),
    # 2. AssistantMessage - explanation text
    create_mock_assistant_message("I'll read the file at /tmp/test.txt for you."),
    # 3. AssistantMessage - tool use
    create_mock_assistant_message_with_tool_use(
        tool_name="Read",
        tool_input={"file_path": "/tmp/test.txt"},
        tool_use_id=MOCK_MULTI_TURN_TOOL_ID,
    ),
    # 4. UserMessage - tool result
    create_mock_user_message_with_tool_result(
        tool_use_id=MOCK_MULTI_TURN_TOOL_ID,
        content="<tool_use_error>File does not exist.</tool_use_error>",
        is_error=True,
    ),
    # 5. AssistantMessage - final response
    create_mock_assistant_message("The file at `/tmp/test.txt` does not exist. Would you like me to create it?"),
    # 6. ResultMessage - aggregated result with num_turns=2
    MOCK_MULTI_TURN_RESULT,
]

# Expected values for LLMObs assertions (based on captured data)
EXPECTED_INPUT_TOKENS = 3 + 12742 + 1854  # input + cache_creation + cache_read = 14599
EXPECTED_OUTPUT_TOKENS = 5
EXPECTED_TOTAL_TOKENS = EXPECTED_INPUT_TOKENS + EXPECTED_OUTPUT_TOKENS

# Expected values for multi-turn (10 + 443 + 28863 = 29316 input, 147 output)
# Based on real captured data from captured-multi-turn.json
EXPECTED_MULTI_TURN_INPUT_TOKENS = 10 + 443 + 28863  # = 29316
EXPECTED_MULTI_TURN_OUTPUT_TOKENS = 147
EXPECTED_MULTI_TURN_TOTAL_TOKENS = EXPECTED_MULTI_TURN_INPUT_TOKENS + EXPECTED_MULTI_TURN_OUTPUT_TOKENS
