import os

import openai
import vcr


mock_openai_completions_response = openai.types.Completion(
    id="chatcmpl-B7PuLoKEQgMd5DQzzN9i4mBJ7OwwO",
    choices=[
        openai.types.CompletionChoice(
            finish_reason="stop", index=0, logprobs=None, text="Hello! How can I assist you today?"
        ),
        openai.types.CompletionChoice(
            finish_reason="stop", index=1, logprobs=None, text="Hello! How can I assist you today?"
        ),
    ],
    created=1741107585,
    model="gpt-3.5-turbo",
    object="text_completion",
    system_fingerprint=None,
)
mock_openai_chat_completions_response = openai.types.chat.ChatCompletion(
    id="chatcmpl-B7RpFsUAXS7aCZlt6jCshVym5yLhN",
    choices=[
        openai.types.chat.chat_completion.Choice(
            finish_reason="stop",
            index=0,
            logprobs=None,
            message=openai.types.chat.ChatCompletionMessage(
                content="The 2020 World Series was played at Globe Life Field in Arlington, Texas.",
                refusal=None,
                role="assistant",
                audio=None,
                function_call=None,
                tool_calls=None,
            ),
        ),
        openai.types.chat.chat_completion.Choice(
            finish_reason="stop",
            index=1,
            logprobs=None,
            message=openai.types.chat.ChatCompletionMessage(
                content="The 2020 World Series was played at Globe Life Field in Arlington, Texas.",
                refusal=None,
                role="assistant",
                audio=None,
                function_call=None,
                tool_calls=None,
            ),
        ),
    ],
    created=1741114957,
    model="gpt-3.5-turbo",
    object="chat.completion",
    service_tier="default",
    system_fingerprint=None,
)
multi_message_input = [
    {
        "content": "You are a helpful assistant.",
        "role": "system",
    },
    {"content": "Who won the world series in 2020?", "role": "user"},
    {"content": "The Los Angeles Dodgers won the World Series in 2020.", "role": "assistant"},
    {"content": "Where was it played?", "role": "user"},
]

chat_completion_input_description = """
    David Nguyen is a sophomore majoring in computer science at Stanford University and has a GPA of 3.8.
    David is an active member of the university's Chess Club and the South Asian Student Association.
    He hopes to pursue a career in software engineering after graduating.
    """
chat_completion_custom_functions = [
    {
        "type": "function",
        "function": {
            "name": "extract_student_info",
            "description": "Get the student information from the body of the input text",
            "parameters": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Name of the person"},
                    "major": {"type": "string", "description": "Major subject."},
                    "school": {"type": "string", "description": "The university name."},
                    "grades": {"type": "integer", "description": "GPA of the student."},
                    "clubs": {
                        "type": "array",
                        "description": "School clubs for extracurricular activities. ",
                        "items": {"type": "string", "description": "Name of School Club"},
                    },
                },
            },
        },
    },
]
function_call_expected_output = {
    "content": "",
    "role": "assistant",
    "tool_calls": [
        {
            "name": "extract_student_info",
            "arguments": {
                "name": "David Nguyen",
                "major": "computer science",
                "school": "Stanford University",
                "grades": 3.8,
                "clubs": ["Chess Club", "South Asian Student Association"],
            },
        }
    ],
}
tool_call_expected_output = function_call_expected_output.copy()
tool_call_expected_output["tool_calls"][0]["tool_id"] = "call_FJStsEjxdODw9tBmQRRkm6vY"
tool_call_expected_output["tool_calls"][0]["type"] = "function"

response_tool_function = [
    {
        "type": "function",
        "name": "get_current_weather",
        "description": "Get the current weather in a given location",
        "parameters": {
            "type": "object",
            "properties": {
                "location": {
                    "type": "string",
                    "description": "The city and state, e.g. San Francisco, CA",
                },
                "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
            },
            "required": ["location", "unit"],
        },
    }
]
response_tool_function_expected_output = [
    {
        "tool_calls": [
            {
                "name": "get_current_weather",
                "type": "function_call",
                "tool_id": "call_tjEzTywkXuBUO42ugPFnQYqi",
                "arguments": {"location": "Boston, MA", "unit": "celsius"},
            },
        ],
        "role": "assistant",
    }
]

response_tool_function_expected_output_streamed = [
    {
        "tool_calls": [
            {
                "name": "get_current_weather",
                "type": "function_call",
                "tool_id": "call_lGe2JKQEBSP15opZ3KfxtEUC",
                "arguments": {"location": "Boston, MA", "unit": "celsius"},
            },
        ],
        "role": "assistant",
    }
]


def mock_response_mcp_tool_call():
    from openai.types.responses import Response

    return Response.model_construct(
        model="gpt-5-2025-08-07",
        output=[
            {
                "id": "mcpl_0f873afd7ff4f5b30168ffa1f4a5cc81a09c93896d6090f9eb",
                "server_label": "dice_roller",
                "tools": [
                    {
                        "input_schema": {
                            "type": "object",
                            "properties": {
                                "notation": {
                                    "type": "string",
                                    "description": (
                                        'Dice notation. Examples: "1d20+5" (basic), "2d20kh1" (advantage), '
                                        '"2d20kl1" (disadvantage), "4d6kh3" (stats), "3d6!" (exploding), '
                                        '"4d6r1" (reroll 1s), "5d10>7" (successes)'
                                    ),
                                },
                                "label": {
                                    "type": "string",
                                    "description": 'Optional label e.g., "Attack roll", "Fireball damage"',
                                },
                                "verbose": {
                                    "type": "boolean",
                                    "description": "Show detailed breakdown of individual dice results",
                                },
                            },
                            "required": ["notation"],
                            "additionalProperties": False,
                            "$schema": "http://json-schema.org/draft-07/schema#",
                        },
                        "name": "dice_roll",
                        "annotations": {"read_only": False},
                        "description": (
                            "Roll dice using standard notation. IMPORTANT: For D&D advantage use '2d20kh1' (NOT '2d20')"
                        ),
                    },
                ],
                "type": "mcp_list_tools",
            },
            {
                "id": "rs_0f873afd7ff4f5b30168ffa1f5d91c81a0890e78a4873fbc1b",
                "summary": [],
                "type": "reasoning",
            },
            {
                "id": "mcp_0f873afd7ff4f5b30168ffa1f7ddec81a0a114abda192da6b3",
                "arguments": '{"notation":"2d4+1","label":"2d4+1 roll","verbose":true}',
                "name": "dice_roll",
                "server_label": "dice_roller",
                "type": "mcp_call",
                "output": "You rolled 2d4+1 for 2d4+1 roll:\n🎲 Total: 8\n📊 Breakdown: 2d4:[3,4] + 1",
            },
            {
                "id": "msg_0f873afd7ff4f5b30168ffa1f8e7f881a0aaec9b8bbc246900",
                "content": [
                    {
                        "text": "You rolled 2d4+1:\n- Total: 8\n- Breakdown: 2d4 → [3, 4] + 1",
                        "type": "output_text",
                    }
                ],
                "role": "assistant",
                "type": "message",
            },
        ],
        temperature=1.0,
        top_p=1.0,
        tool_choice="auto",
        truncation="disabled",
        text={"format": {"type": "text"}, "verbosity": "medium"},
        usage={
            "input_tokens": 642,
            "input_tokens_details": {"cached_tokens": 0},
            "output_tokens": 206,
            "output_tokens_details": {"reasoning_tokens": 128},
            "total_tokens": 848,
        },
    )


# VCR is used to capture and store network requests made to OpenAI.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.


# To (re)-generate the cassettes: pass a real OpenAI API key with
# OPENAI_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
def get_openai_vcr(subdirectory_name=""):
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/%s" % subdirectory_name),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "OpenAI-Organization", "api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


def assert_prompt_tracking(span_event, prompt_id, prompt_version, variables, expected_chat_template, expected_messages):
    """Helper to assert prompt tracking metadata and template extraction."""
    assert "prompt" in span_event["meta"]["input"]
    actual_prompt = span_event["meta"]["input"]["prompt"]
    assert actual_prompt["id"] == prompt_id
    assert actual_prompt["version"] == prompt_version
    assert actual_prompt["variables"] == variables
    assert "chat_template" in actual_prompt
    assert actual_prompt["chat_template"] == expected_chat_template
    assert span_event["meta"]["input"]["messages"] == expected_messages
