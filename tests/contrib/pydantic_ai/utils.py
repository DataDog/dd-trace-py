PYDANTIC_AI_TAGS = {
    "ml_app": "<ml-app-name>",
    "service": "tests.contrib.pydantic_ai",
    "integration": "pydantic_ai",
}


# pydantic-ai's own defaults for an agent that configures none of these, at the versions riotfile.py
# pins. They are framework defaults rather than caller choices, which is why they are asserted here
# once instead of being repeated per test. They are NOT a framework invariant: end_strategy defaults
# to "graceful" at 2.x, so adding a 2.x pin will fail here on purpose.
DEFAULT_DATA_CONTRACTS = {"output": {"name": "str"}}
DEFAULT_AGENT_SETTINGS = {"retries": 1, "tool_retries": 1, "end_strategy": "early"}


def expected_calculate_square_tool():
    return [
        {
            "name": "calculate_square_tool",
            "description": "Calculates the square of a number",
            "parameters": {"x": {"type": "integer", "required": True}},
        }
    ]


def expected_foo_tool():
    # No parameters key: a tool that takes no arguments has nothing to say, and an empty dict is
    # exactly what the manifest must not emit.
    return [
        {
            "name": "foo_tool",
            "description": "Return foo string",
        }
    ]


def expected_agent_manifest(
    name="test_agent",
    model="gpt-4o",
    model_provider=None,
    instructions=None,
    system_prompt=None,
    model_params=None,
    tools=None,
    **extra_fields,
) -> dict:
    """Build the agent manifest a test expects.

    One flat document. A field with no value does not appear at all, which is what omit-when-absent
    means on the wire, so a test that omits an argument is asserting the key is absent.

    model_provider is accepted and ignored: pydantic-ai has never emitted it.
    """
    manifest = {"framework": "PydanticAI"}
    if name:
        manifest["name"] = name
    if instructions:
        manifest["instructions"] = instructions
    if system_prompt:
        manifest["system_prompts"] = [system_prompt]
    if model:
        manifest["model"] = model
    if model_params:
        # Reported under pydantic-ai's own spelling: the integration allowlists keys but does not
        # rename them, so what a test configures is what it expects on the wire.
        manifest["model_settings"] = dict(model_params)
    if tools:
        manifest["tools"] = tools
    manifest["data_contracts"] = {"output": dict(DEFAULT_DATA_CONTRACTS["output"])}
    manifest["agent_settings"] = dict(DEFAULT_AGENT_SETTINGS)
    manifest.update(extra_fields)
    return manifest


def expected_agent_metadata(**kwargs) -> dict:
    return {"_dd": {"agent_manifest": expected_agent_manifest(**kwargs)}}


def calculate_square_tool(x: int) -> int:
    """Calculates the square of a number"""
    return x * x


def foo_tool() -> str:
    """Return foo string"""
    return "foo"
