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


# The RFC-shaped manifest always carries an ``output_type`` and ``agent_settings`` block: an agent
# with no structured output still defaults to ``str``, and ``retries`` / ``end_strategy`` are present
# on every supported pydantic-ai version (0.8.1 / 1.0.0 / 1.63.0). These are the per-test baselines.
DEFAULT_OUTPUT_TYPE = {"name": "str"}
DEFAULT_AGENT_SETTINGS = {"retries": 1, "end_strategy": "early"}


def expected_agent_metadata(
    instructions=None,
    model_settings=None,
    tools=None,
    metadata=None,
    capabilities=None,
    handoffs=None,
    output_type=None,
    agent_settings=None,
    instructions_is_dynamic=None,
    instructions_functions=None,
    system_prompts=None,
    system_prompts_is_dynamic=None,
    system_prompt_functions=None,
) -> dict:
    manifest = {
        "framework": "PydanticAI",
        "name": "test_agent",
        "model": "gpt-4o",
        "model_settings": model_settings,
        "instructions": instructions,
        "tools": tools if tools is not None else [],
        "output_type": output_type if output_type is not None else dict(DEFAULT_OUTPUT_TYPE),
        "agent_settings": agent_settings if agent_settings is not None else dict(DEFAULT_AGENT_SETTINGS),
    }
    if instructions_is_dynamic is not None:
        manifest["instructions_is_dynamic"] = instructions_is_dynamic
    # The integration adds metadata / capabilities / handoffs / the prompt-enrichment fields only when
    # present on the agent; callers without them expect no key.
    if metadata is not None:
        manifest["metadata"] = metadata
    if capabilities is not None:
        manifest["capabilities"] = capabilities
    if handoffs is not None:
        manifest["handoffs"] = handoffs
    if instructions_functions is not None:
        manifest["instructions_functions"] = instructions_functions
    if system_prompts is not None:
        manifest["system_prompts"] = system_prompts
    if system_prompts_is_dynamic is not None:
        manifest["system_prompts_is_dynamic"] = system_prompts_is_dynamic
    if system_prompt_functions is not None:
        manifest["system_prompt_functions"] = system_prompt_functions
    return {"_dd": {"agent_manifest": manifest}}


def calculate_square_tool(x: int) -> int:
    """Calculates the square of a number"""
    return x * x


def foo_tool() -> str:
    """Return foo string"""
    return "foo"
