import os

import vcr


chat_completion_input_description = """
    David Nguyen is a sophomore majoring in computer science at Stanford University and has a GPA of 3.8.
    David is an active member of the university's Chess Club and the South Asian Student Association.
    He hopes to pursue a career in software engineering after graduating.
    """
chat_completion_custom_functions = [
    {
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
]


def iswrapped(obj):
    return hasattr(obj, "__dd_wrapped__")


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
