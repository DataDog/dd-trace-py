import os

from anthropic.types import Message
from anthropic.types import TextBlock
from anthropic.types import Usage
import vcr


# VCR is used to capture and store network requests made to Anthropic.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.


# To (re)-generate the cassettes: pass a real Anthropic API key with
# ANTHROPIC_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
def get_request_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


# Anthropic Tools


tools = [
    {
        "name": "get_weather",
        "description": "Get the weather for a specific location",
        "input_schema": {
            "type": "object",
            "properties": {"location": {"type": "string"}},
        },
    }
]

MOCK_MESSAGES_CREATE_REQUEST = Message(
    id="chatcmpl-0788cc8c-bdee-4bb3-8952-ef1ff3243af5",
    content=[TextBlock(text='THE BEST-SELLING BOOK OF ALL TIME IS "DON', type="text")],
    model="claude-3-opus-20240229",
    role="assistant",
    stop_reason="max_tokens",
    stop_sequence=None,
    type="message",
    usage=Usage(input_tokens=32, output_tokens=15),
)
