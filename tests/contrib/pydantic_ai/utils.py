PYDANTIC_AI_TAGS = {
    "ml_app": "<ml-app-name>",
    "service": "tests.contrib.pydantic_ai",
    "integration": "pydantic_ai",
}


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


def expected_agent_metadata(instructions=None, system_prompt=None, model_settings=None, tools=None) -> dict:
    return {
        "_dd": {
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
    }


def calculate_square_tool(x: int) -> int:
    """Calculates the square of a number"""
    return x * x


def foo_tool() -> str:
    """Return foo string"""
    return "foo"
