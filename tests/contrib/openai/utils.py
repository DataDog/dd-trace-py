import os

import vcr


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


def _expected_llmobs_tags(error=None):
    expected_tags = [
        "version:",
        "env:",
        "service:",
        "source:integration",
        "ml_app:unnamed-ml-app",
    ]
    if error:
        expected_tags.append("error:1")
        expected_tags.append("error_type:{}".format(error))
    else:
        expected_tags.append("error:0")
    return expected_tags


def _expected_llmobs_span_event(
    span,
    model,
    input_messages,
    output_messages,
    parameters,
    token_metrics,
    session_id=None,
    error=None,
    error_message=None,
):
    span_event = {
        "span_id": str(span.span_id),
        "trace_id": "{:x}".format(span.trace_id),
        "parent_id": "",
        "session_id": "{:x}".format(session_id or span.trace_id),
        "name": span.name,
        "tags": _expected_llmobs_tags(error=error),
        "start_ns": span.start_ns,
        "duration": span.duration_ns,
        "error": 1 if error else 0,
        "meta": {
            "span.kind": "llm",
            "model_name": model,
            "model_provider": "openai",
            "input": {"messages": input_messages, "parameters": parameters},
            "output": {"messages": output_messages},
        },
        "metrics": token_metrics,
    }
    if error:
        span_event["meta"]["error.message"] = error_message
    return span_event
