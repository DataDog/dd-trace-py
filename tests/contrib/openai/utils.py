import os

import vcr


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
