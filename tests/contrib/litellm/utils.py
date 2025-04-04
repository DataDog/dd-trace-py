import os

import vcr


CASETTE_EXTENSION = ".yaml"


# VCR is used to capture and store network requests made to Anthropic.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.
def get_request_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


# Get the name of the cassette to use for a given test
# All LiteLLM requests that use Open AI get routed to the chat completions endpoint,
# so we can reuse the same cassette for each combination of stream and n
def get_cassette_name(stream, n):
    stream_suffix = "_stream" if stream else ""
    choice_suffix = "_multiple_choices" if n > 1 else ""
    return "completion" + stream_suffix + choice_suffix + CASETTE_EXTENSION
