from tests.llmobs._utils import _expected_llmobs_non_llm_span_event

def expected_run_agent_span_event(span, output, token_metrics, input_value="Hello, world!", instructions=None, tools=None, span_links=None):
    return _expected_llmobs_non_llm_span_event(
        span,
        "agent",
        input_value=input_value,
        output_value=output,
        metadata={"instructions": instructions, "system_prompts": (), "tools": tools or []},
        token_metrics=token_metrics,
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.pydantic_ai"},
        span_links=span_links,
    )

def get_usage(result):
    usage = result.usage()
    token_metrics = {
        "input_tokens": getattr(usage, "request_tokens", 0),
        "output_tokens": getattr(usage, "response_tokens", 0),
        "total_tokens": getattr(usage, "total_tokens", 0),
    }
    return token_metrics

def calculate_square_tool(x: int) -> int:
    return x * x