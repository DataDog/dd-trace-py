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


# StreamEvent (partial-message streaming) is only re-exported from the package root in
# newer SDKs (>=0.1.49); on older matrix versions (0.0.23, 0.1.29) it lives only in
# claude_agent_sdk.types. Import defensively so this module still loads everywhere.
try:
    from claude_agent_sdk import StreamEvent
except ImportError:
    from claude_agent_sdk.types import StreamEvent


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


def create_mock_assistant_message(
    text: str, model: str = MOCK_MODEL, usage: dict = None, message_id: str = None, parent_tool_use_id: str = None
) -> AssistantMessage:
    """Create a mock AssistantMessage for testing."""
    msg = AssistantMessage(
        content=[TextBlock(text=text)],
        model=model,
    )
    if usage is not None:
        msg.usage = usage
    # message_id was added in newer claude-agent-sdk versions; set via setattr so the
    # helper stays compatible with older SDKs whose AssistantMessage lacks the field.
    if message_id is not None:
        msg.message_id = message_id
    # parent_tool_use_id is None for the main agent, the spawning tool-use id for a subagent.
    if parent_tool_use_id is not None:
        msg.parent_tool_use_id = parent_tool_use_id
    return msg


def create_mock_assistant_message_with_tool_use(
    tool_calls: list[tuple[str, dict, str]],
    model: str = MOCK_MODEL,
) -> AssistantMessage:
    """Create a mock AssistantMessage with one or more tool use blocks.

    Args:
        tool_calls: list of (tool_name, tool_input, tool_use_id) tuples. A
            single-element list produces a normal tool-use message; a
            multi-element list produces a parallel tool-use message.
    """
    return AssistantMessage(
        content=[
            ToolUseBlock(id=tool_use_id, name=tool_name, input=tool_input)
            for tool_name, tool_input, tool_use_id in tool_calls
        ],
        model=model,
    )


def create_mock_user_message_with_tool_result(
    tool_results: list[tuple[str, str]],
    is_error: bool = False,
) -> UserMessage:
    """Create a mock UserMessage containing one or more ToolResultBlocks.

    Args:
        tool_results: list of (tool_use_id, content) tuples.
        is_error: if True, every result block is marked as an error.
    """
    return UserMessage(
        content=[
            ToolResultBlock(tool_use_id=tool_use_id, content=content, is_error=is_error)
            for tool_use_id, content in tool_results
        ],
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


def create_mock_stream_event(event: dict, parent_tool_use_id: str = None) -> StreamEvent:
    """Create a mock StreamEvent wrapping a raw Anthropic streaming event dict.

    ``parent_tool_use_id`` is None for the main agent's stream, the spawning tool-use id for a
    subagent's — the SDK sets it on every StreamEvent so interleaved subagent streams stay distinct.
    """
    return StreamEvent(
        uuid="test-uuid",
        session_id="test-session-id",
        event=event,
        parent_tool_use_id=parent_tool_use_id,
    )


def create_mock_status_message(status: str = "requesting") -> SystemMessage:
    """A ``SystemMessage(subtype="status")`` — a lifecycle ping the CLI only emits when
    partial streaming is on. The integration filters these back out when it enabled the flag.
    """
    return SystemMessage(
        subtype="status",
        data={"type": "system", "subtype": "status", "status": status, "session_id": "test-session-id"},
    )


MOCK_SYSTEM_MESSAGE = create_mock_system_message()
MOCK_ASSISTANT_RESPONSE = create_mock_assistant_message("4")
MOCK_ASSISTANT_RESPONSE_TWO = create_mock_assistant_message("8")
MOCK_ASSISTANT_RESPONSE_WITH_USAGE = create_mock_assistant_message("4", usage=MOCK_ASSISTANT_USAGE)
MOCK_RESULT_MESSAGE = create_mock_result_message()


MOCK_DOUBLE_ASSISTANT_NO_TOOLS_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_ASSISTANT_RESPONSE,
    MOCK_ASSISTANT_RESPONSE_TWO,
    MOCK_RESULT_MESSAGE,
]


MOCK_SHARED_MESSAGE_ID = "msg_01SharedTurnAaaaaaaaaaaaaa"
MOCK_DEDUPE_ASSISTANT_CHUNK_ONE = create_mock_assistant_message(
    "Let me think.", usage=MOCK_ASSISTANT_USAGE, message_id=MOCK_SHARED_MESSAGE_ID
)
MOCK_DEDUPE_ASSISTANT_CHUNK_TWO = create_mock_assistant_message(
    "The answer is 4.", usage=MOCK_ASSISTANT_USAGE, message_id=MOCK_SHARED_MESSAGE_ID
)
MOCK_DEDUPE_ASSISTANT_SAME_MESSAGE_ID_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_DEDUPE_ASSISTANT_CHUNK_ONE,
    MOCK_DEDUPE_ASSISTANT_CHUNK_TWO,
    MOCK_RESULT_MESSAGE,
]


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

# Reproduces an API error surfaced in the assistant message content (e.g. an
# overloaded_error). The SDK maps the uncategorized error to the "unknown" literal
# while the descriptive payload lives in a TextBlock.
MOCK_ASSISTANT_MESSAGE_ERROR_TYPE = "unknown"
MOCK_ASSISTANT_MESSAGE_ERROR_TEXT = (
    'API Error: {"type":"error","error":{"details":null,"type":"overloaded_error",'
    '"message":"Overloaded"},"request_id":"req_011Cbd5D168oye3XGdgSjVog"}'
)
# NOTE: assign ``error`` after construction rather than as a constructor kwarg — older
# claude-agent-sdk versions (e.g. 0.0.23) don't accept ``error`` in AssistantMessage.__init__.
MOCK_ASSISTANT_MESSAGE_WITH_ERROR_TEXT = AssistantMessage(
    content=[TextBlock(text=MOCK_ASSISTANT_MESSAGE_ERROR_TEXT)], model=MOCK_MODEL
)
MOCK_ASSISTANT_MESSAGE_WITH_ERROR_TEXT.error = MOCK_ASSISTANT_MESSAGE_ERROR_TYPE
MOCK_ASSISTANT_MESSAGE_ERROR_TEXT_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_ASSISTANT_MESSAGE_WITH_ERROR_TEXT,
    MOCK_RESULT_MESSAGE,
]

MOCK_QUERY_RESPONSE_SEQUENCE_WITH_USAGE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_ASSISTANT_RESPONSE_WITH_USAGE,
    MOCK_RESULT_MESSAGE,
]


# Simulates what the SDK stream looks like once include_partial_messages is on:
# the AssistantMessage.usage carries only the message_start snapshot (output_tokens=1),
# while the true per-turn output (120) shows up in the message_delta StreamEvent. The
# ResultMessage reports the same cumulative total (120).
MOCK_PARTIAL_TURN_MESSAGE_ID = "msg_01PartialTurnAaaaaaaaaaaa"
MOCK_PARTIAL_SNAPSHOT_USAGE = {
    "input_tokens": 10,
    "output_tokens": 1,  # message_start snapshot — pre-generation
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
}
MOCK_PARTIAL_TRUE_OUTPUT_TOKENS = 120
MOCK_PARTIAL_RESULT_USAGE = {
    "input_tokens": 10,
    "cache_creation_input_tokens": 0,
    "cache_read_input_tokens": 0,
    "output_tokens": MOCK_PARTIAL_TRUE_OUTPUT_TOKENS,
}
MOCK_PARTIAL_MESSAGES_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    create_mock_status_message(),
    create_mock_stream_event(
        {"type": "message_start", "message": {"id": MOCK_PARTIAL_TURN_MESSAGE_ID, "usage": {"output_tokens": 1}}}
    ),
    create_mock_assistant_message(
        "The answer is 4.", usage=MOCK_PARTIAL_SNAPSHOT_USAGE, message_id=MOCK_PARTIAL_TURN_MESSAGE_ID
    ),
    create_mock_stream_event({"type": "message_delta", "usage": {"output_tokens": MOCK_PARTIAL_TRUE_OUTPUT_TOKENS}}),
    create_mock_result_message(usage=MOCK_PARTIAL_RESULT_USAGE),
]


# Same as MOCK_PARTIAL_MESSAGES_SEQUENCE but with a non-"requesting" status SystemMessage mixed in.
# When we force partial streaming we filter only our own noise — the "requesting" ping and the
# StreamEvents. Other status messages (here a compaction result) are caller-visible and not gated
# on partial streaming, so they must survive the filter and reach the caller.
MOCK_COMPACTION_STATUS_VALUE = "compacted"
MOCK_PARTIAL_MESSAGES_STATUS_PASSTHROUGH_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    create_mock_status_message(),  # "requesting" ping — ours, filtered back out
    create_mock_stream_event(
        {"type": "message_start", "message": {"id": MOCK_PARTIAL_TURN_MESSAGE_ID, "usage": {"output_tokens": 1}}}
    ),
    create_mock_status_message(status=MOCK_COMPACTION_STATUS_VALUE),  # caller-visible — must pass through
    create_mock_assistant_message(
        "The answer is 4.", usage=MOCK_PARTIAL_SNAPSHOT_USAGE, message_id=MOCK_PARTIAL_TURN_MESSAGE_ID
    ),
    create_mock_stream_event({"type": "message_delta", "usage": {"output_tokens": MOCK_PARTIAL_TRUE_OUTPUT_TOKENS}}),
    create_mock_result_message(usage=MOCK_PARTIAL_RESULT_USAGE),
]


# Simulates a pre-0.1.49 SDK where AssistantMessage carries no usage at all (the field was
# added in 0.1.49). The only token source is the partial-message stream: message_start
# carries the input/cache tokens and message_delta the true cumulative output. The
# integration must synthesize the whole usage block from these events.
MOCK_PARTIAL_NO_USAGE_MESSAGE_ID = "msg_01PartialNoUsageAaaaaaaaaa"
MOCK_PARTIAL_MESSAGES_NO_ASSISTANT_USAGE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    create_mock_status_message(),
    create_mock_stream_event(
        {
            "type": "message_start",
            "message": {
                "id": MOCK_PARTIAL_NO_USAGE_MESSAGE_ID,
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 1,  # pre-generation snapshot — ignored
                },
            },
        }
    ),
    # message_delta carries the true output but no id of its own; the integration attributes it
    # to the id from the preceding message_start (its _partial_current_id cursor), so the usage
    # lands under MOCK_PARTIAL_NO_USAGE_MESSAGE_ID. It streams before the assembled AssistantMessage.
    create_mock_stream_event({"type": "message_delta", "usage": {"output_tokens": MOCK_PARTIAL_TRUE_OUTPUT_TOKENS}}),
    # Old SDKs (< 0.1.49) omit message_id on AssistantMessage entirely. With no id to match on, the
    # usage is joined back to this turn via that same streaming message_start id (stamped when the
    # turn began buffering), which is why the delta above had to be keyed under it.
    create_mock_assistant_message("The answer is 4.", usage=None, message_id=None),
    create_mock_result_message(usage=MOCK_PARTIAL_RESULT_USAGE),
]


# Simulates an older SDK version where one model message (a text block plus a tool_use block) is
# split into two message_id-less AssistantMessages. The integration must join them by the
# streaming message id into ONE llm span carrying the whole message's tokens.
MOCK_PARTIAL_SPLIT_MESSAGE_ID = "msg_01PartialSplitAaaaaaaaaaa"
MOCK_PARTIAL_SPLIT_TOOL_USE_ID = "toolu_01PartialSplitBbbbbbbbbb"
MOCK_PARTIAL_SPLIT_FINAL_MESSAGE_ID = "msg_01PartialSplitCccccccccc"
MOCK_PARTIAL_SPLIT_FINAL_OUTPUT_TOKENS = 30
MOCK_PARTIAL_MESSAGES_SPLIT_TEXT_TOOL_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    create_mock_status_message(),
    create_mock_stream_event(
        {
            "type": "message_start",
            "message": {
                "id": MOCK_PARTIAL_SPLIT_MESSAGE_ID,
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 1,  # pre-generation snapshot — ignored
                },
            },
        }
    ),
    # One message, two chunks (no message_id): the text block, then the tool_use block.
    create_mock_assistant_message("I'll run the command.", usage=None, message_id=None),
    create_mock_assistant_message_with_tool_use([("Bash", {"command": "echo alpha"}, MOCK_PARTIAL_SPLIT_TOOL_USE_ID)]),
    # The message's single true output count streams in at the end, after both chunks.
    create_mock_stream_event({"type": "message_delta", "usage": {"output_tokens": MOCK_PARTIAL_TRUE_OUTPUT_TOKENS}}),
    # The tool result ends the first turn and flushes the merged llm span.
    create_mock_user_message_with_tool_result([(MOCK_PARTIAL_SPLIT_TOOL_USE_ID, "alpha")]),
    # The model closes the turn with a final text message (its own streaming id and output count).
    create_mock_stream_event(
        {
            "type": "message_start",
            "message": {
                "id": MOCK_PARTIAL_SPLIT_FINAL_MESSAGE_ID,
                "usage": {
                    "input_tokens": 10,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                    "output_tokens": 1,  # pre-generation snapshot — ignored
                },
            },
        }
    ),
    create_mock_assistant_message("The command printed alpha.", usage=None, message_id=None),
    create_mock_stream_event(
        {"type": "message_delta", "usage": {"output_tokens": MOCK_PARTIAL_SPLIT_FINAL_OUTPUT_TOKENS}}
    ),
    create_mock_result_message(usage=MOCK_PARTIAL_RESULT_USAGE),
]


# Simulates a main agent and a subagent streaming concurrently: their StreamEvents interleave in
# one stream, distinguished only by parent_tool_use_id. Both message_starts arrive before either
# id-less message_delta, so a single shared cursor would attribute both deltas to whichever
# message_start came last, clobbering one turn's output tokens. Scoping the cursor by
# parent_tool_use_id keeps each turn's true output attributed to its own message.
MOCK_SUBAGENT_TOOL_USE_ID = "toolu_01SubagentScopeAaaaaaaaa"
MOCK_SUBAGENT_MAIN_MESSAGE_ID = "msg_01SubagentMainAaaaaaaaaa"
MOCK_SUBAGENT_CHILD_MESSAGE_ID = "msg_01SubagentChildBbbbbbbbb"
MOCK_SUBAGENT_MAIN_OUTPUT_TOKENS = 100
MOCK_SUBAGENT_CHILD_OUTPUT_TOKENS = 50
MOCK_SUBAGENT_INTERLEAVED_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    create_mock_status_message(),
    # Both turns begin streaming before either produces output: main first, then the subagent.
    create_mock_stream_event(
        {
            "type": "message_start",
            "message": {
                "id": MOCK_SUBAGENT_MAIN_MESSAGE_ID,
                "usage": {"input_tokens": 10, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
        }
    ),
    create_mock_stream_event(
        {
            "type": "message_start",
            "message": {
                "id": MOCK_SUBAGENT_CHILD_MESSAGE_ID,
                "usage": {"input_tokens": 20, "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0},
            },
        },
        parent_tool_use_id=MOCK_SUBAGENT_TOOL_USE_ID,
    ),
    # Each turn's id-less true output must land on its own message_start via the scoped cursor.
    create_mock_stream_event({"type": "message_delta", "usage": {"output_tokens": MOCK_SUBAGENT_MAIN_OUTPUT_TOKENS}}),
    create_mock_stream_event(
        {"type": "message_delta", "usage": {"output_tokens": MOCK_SUBAGENT_CHILD_OUTPUT_TOKENS}},
        parent_tool_use_id=MOCK_SUBAGENT_TOOL_USE_ID,
    ),
    # Both turns arrive as id-less AssistantMessages (pre-0.1.49 style); scope alone joins the usage.
    create_mock_assistant_message(
        "Subagent finished.", usage=None, message_id=None, parent_tool_use_id=MOCK_SUBAGENT_TOOL_USE_ID
    ),
    create_mock_assistant_message("Main agent finished.", usage=None, message_id=None),
    create_mock_result_message(usage=MOCK_PARTIAL_RESULT_USAGE),
]


MOCK_READ_TOOL_ID = "toolu_01C4Thx957VoSn21zERxbeQX"
MOCK_TOOL_USE_ASSISTANT = create_mock_assistant_message_with_tool_use(
    [("Read", {"file_path": "/etc/hostname"}, MOCK_READ_TOOL_ID)],
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
    [("Bash", MOCK_BASH_TOOL_INPUT, MOCK_BASH_TOOL_ID)],
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
    [("Grep", MOCK_GREP_TOOL_INPUT, MOCK_GREP_TOOL_ID)],
)
MOCK_GREP_TOOL_RESPONSE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_GREP_TOOL_ASSISTANT,
    MOCK_RESULT_MESSAGE,
]

MOCK_TOOL_RESULT_USER_READ = create_mock_user_message_with_tool_result(
    [(MOCK_READ_TOOL_ID, "myhost.local")],
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

MOCK_DEDUPE_TURN_A_ID = "msg_01DedupeTurnAaaaaaaaaaaaaa"
MOCK_DEDUPE_TEXT_CHUNK = create_mock_assistant_message(
    "Let me read that file.", usage=MOCK_ASSISTANT_USAGE, message_id=MOCK_DEDUPE_TURN_A_ID
)
MOCK_DEDUPE_TOOL_USE_CHUNK = create_mock_assistant_message_with_tool_use(
    [("Read", {"file_path": "/etc/hostname"}, MOCK_READ_TOOL_ID)],
)
MOCK_DEDUPE_TOOL_USE_CHUNK.usage = MOCK_ASSISTANT_USAGE
MOCK_DEDUPE_TOOL_USE_CHUNK.message_id = MOCK_DEDUPE_TURN_A_ID
MOCK_DEDUPE_TOOL_SPLIT_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_DEDUPE_TEXT_CHUNK,  # turn A, chunk 1 (text)
    MOCK_DEDUPE_TOOL_USE_CHUNK,  # turn A, chunk 2 (tool_use) — same message_id → merged
    MOCK_TOOL_RESULT_USER_READ,  # tool result → finishes tool span
    MOCK_FINAL_ASSISTANT,  # turn B (text) → second llm span
    MOCK_MULTI_TURN_RESULT_MESSAGE,
]


MOCK_PARALLEL_BASH_TOOL_IDS = (
    "toolu_parallel_01_aaaaaaaaaaaaaaaaaa",
    "toolu_parallel_02_bbbbbbbbbbbbbbbbbb",
    "toolu_parallel_03_cccccccccccccccccc",
)
MOCK_PARALLEL_BASH_TOOL_USE_ASSISTANT = create_mock_assistant_message_with_tool_use(
    [
        ("Bash", {"command": "echo first"}, MOCK_PARALLEL_BASH_TOOL_IDS[0]),
        ("Bash", {"command": "echo second"}, MOCK_PARALLEL_BASH_TOOL_IDS[1]),
        ("Bash", {"command": "echo third"}, MOCK_PARALLEL_BASH_TOOL_IDS[2]),
    ],
)
MOCK_PARALLEL_TOOL_RESULT_USER = create_mock_user_message_with_tool_result(
    [
        (MOCK_PARALLEL_BASH_TOOL_IDS[0], "first"),
        (MOCK_PARALLEL_BASH_TOOL_IDS[1], "second"),
        (MOCK_PARALLEL_BASH_TOOL_IDS[2], "third"),
    ],
)
MOCK_PARALLEL_TOOL_USE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    MOCK_PARALLEL_BASH_TOOL_USE_ASSISTANT,
    MOCK_PARALLEL_TOOL_RESULT_USER,
    MOCK_FINAL_ASSISTANT,
    MOCK_MULTI_TURN_RESULT_MESSAGE,
]


MOCK_TOOL_ERROR_MESSAGE = "Permission denied: /etc/hostname"
MOCK_TOOL_ERROR_USER_READ = create_mock_user_message_with_tool_result(
    [(MOCK_READ_TOOL_ID, MOCK_TOOL_ERROR_MESSAGE)],
    is_error=True,
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


# Same as MOCK_CLIENT_RAW_MESSAGES but interleaved with the partial-streaming events we inject
# when we force include_partial_messages on at init: a StreamEvent and a status SystemMessage.
# Used to exercise the untraced connect(prompt=...) -> receive_response() path, where there is no
# query() span/handler to filter, so filter_forced_partial_noise must strip these back out.
MOCK_CLIENT_RAW_MESSAGES_WITH_PARTIAL_NOISE = [
    EXPECTED_SYSTEM_MESSAGE_DATA,
    {
        "type": "stream_event",
        "uuid": "test-uuid",
        "session_id": "test-session-id",
        "event": {"type": "message_start", "message": {"id": "msg_01ClientNoiseAaaaaaaaaaa", "usage": {}}},
    },
    {
        "type": "system",
        "subtype": "status",
        "status": "requesting",
        "session_id": "test-session-id",
    },
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


# A standalone query() response carrying a status SystemMessage the caller's custom transport
# would surface on its own. When a custom transport is supplied we must NOT force partial
# streaming (the transport is built independently of options), so we must NOT filter — this
# status message must reach the caller untouched.
MOCK_CUSTOM_TRANSPORT_NOISE_SEQUENCE = [
    MOCK_SYSTEM_MESSAGE,
    create_mock_status_message(),
    MOCK_ASSISTANT_RESPONSE,
    MOCK_RESULT_MESSAGE,
]
