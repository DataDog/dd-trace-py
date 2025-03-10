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
    {"role": "system", "content": "You are a helpful assistant."},
    {"role": "user", "content": "Who won the world series in 2020?"},
    {"role": "assistant", "content": "The Los Angeles Dodgers won the World Series in 2020."},
    {"role": "user", "content": "Where was it played?"},
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
