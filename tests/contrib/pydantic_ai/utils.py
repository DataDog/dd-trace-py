from tests.llmobs._utils import _expected_llmobs_non_llm_span_event

def expected_run_agent_span_event(span, output, token_metrics, input_value="Hello, world!"):
    return _expected_llmobs_non_llm_span_event(
        span,
        "agent",
        input_value="Hello, world!",
        output_value=output,
        metadata={"instructions": None, "system_prompts": (), "tools": []},
        token_metrics=token_metrics,
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.pydantic_ai"},
    )

def get_usage(result):
    usage = result.usage() if hasattr(result, "usage") else result
    token_metrics = {
        "input_tokens": getattr(usage, "request_tokens", 0),
        "output_tokens": getattr(usage, "response_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
    }
    return token_metrics