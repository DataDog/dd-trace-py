from typing import Dict

from tests.llmobs._utils import _expected_llmobs_non_llm_span_event


def expected_calculate_square_tool():
    return [
        {
            "name": "calculate_square_tool",
            "description": "Calculates the square of a number",
            "parameters": {"x": {"type": "integer", "required": True}},
        }
    ]


def expected_foo_tool():
    return [
        {
            "name": "foo_tool",
            "description": "Return foo string",
            "parameters": {},
        }
    ]


def expected_agent_metadata(instructions=None, system_prompt=None, model_settings=None, tools=None) -> Dict:
    metadata = {
        "agent_manifest": {
            "framework": "PydanticAI",
            "name": "test_agent",
            "model": "gpt-4o",
            "model_settings": model_settings,
            "instructions": instructions,
            "system_prompts": (system_prompt,) if system_prompt else (),
            "tools": tools if tools is not None else [],
        }
    }
    return metadata


def expected_run_agent_span_event(
    span,
    output,
    token_metrics,
    input_value="Hello, world!",
    instructions=None,
    system_prompt=None,
    model_settings=None,
    span_links=None,
    tools=None,
):
    return _expected_llmobs_non_llm_span_event(
        span,
        "agent",
        input_value=input_value,
        output_value=output,
        metadata=expected_agent_metadata(instructions, system_prompt, model_settings, tools),
        token_metrics=token_metrics,
        tags={"ml_app": "<ml-app-name>", "service": "tests.contrib.pydantic_ai"},
        span_links=span_links,
    )


def expected_run_tool_span_event(span, input_value='{"x":2}', output="4", span_links=None):
    return _expected_llmobs_non_llm_span_event(
        span,
        "tool",
        input_value=input_value,
        output_value=output,
        metadata={"description": "Calculates the square of a number"},
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
    """Calculates the square of a number"""
    return x * x


def foo_tool() -> str:
    """Return foo string"""
    return "foo"
