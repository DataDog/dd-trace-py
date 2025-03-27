import vcr
import os

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
def get_cassette_name(stream, n, include_usage=True):
    stream_suffix = "_stream" if stream else ""
    choice_suffix = "_multiple_choices" if n > 1 else ""
    # include_usage only affects streamed responses
    if stream and not include_usage:
        usage_suffix = "_exclude_usage"
    else:
        usage_suffix = ""
    return "completion" + stream_suffix + choice_suffix + usage_suffix + CASETTE_EXTENSION


def consume_stream(resp, n):
    output_messages = [{"content": "", "role": ""} for _ in range(n)]
    token_metrics = {}
    role = None
    for chunk in resp:
        for choice in chunk["choices"]:
            content = choice["delta"]["content"] or ""
            output_messages[choice.index]["content"] += content
            if not output_messages[choice.index]["role"]:
                role = choice["delta"]["role"] or role
                output_messages[choice.index]["role"] = role

        if "usage" in chunk:
            token_metrics = {
                "input_tokens": chunk["usage"]["prompt_tokens"],
                "output_tokens": chunk["usage"]["completion_tokens"],
                "total_tokens": chunk["usage"]["total_tokens"],
            }
    return output_messages, token_metrics
