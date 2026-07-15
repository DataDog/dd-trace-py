PYDANTIC_AI_TAGS = {
    "ml_app": "<ml-app-name>",
    "service": "tests.contrib.pydantic_ai",
    "integration": "pydantic_ai",
}


def expected_calculate_square_tool():
    return [
        {
            "type": "function",
            "name": "calculate_square_tool",
            "description": "Calculates the square of a number",
            "parameters": {"x": {"type": "integer", "required": True}},
        }
    ]


def expected_foo_tool():
    return [
        {
            "type": "function",
            "name": "foo_tool",
            "description": "Return foo string",
            "parameters": {},
        }
    ]


# The manifest groups the agent's captured definition. ``model`` / ``model_provider`` and -- for a normal
# agent -- an ``output_type`` (defaulting to ``str``) and a ``behaviors.settings`` block (``retries`` /
# ``tool_retries`` / ``end_strategy`` are present on every supported pydantic-ai version 0.8.1 / 1.0.0 /
# 1.63.0) are always present; ``model_settings`` appears only when the agent sets it. Prompts are
# ``{static, dynamic}`` (the dynamic-part shape is covered by the builder-driven ``test_manifest_*``
# tests, so these end-to-end baselines carry only the static text). Per-test baselines.
DEFAULT_OUTPUT_TYPE = {"name": "str"}
DEFAULT_SETTINGS = {"retries": 1, "tool_retries": 1, "end_strategy": "early"}


def expected_agent_metadata(
    *,
    name="test_agent",
    model="gpt-4o",
    model_provider="openai",
    model_settings=None,
    instructions=None,
    system_prompts=None,
    capabilities=None,
    handoffs=None,
    guardrails=None,
    output_type=None,
    settings=None,
    memory=None,
    tool_transforms=None,
    metadata=None,
) -> dict:
    manifest: dict = {"framework": "PydanticAI", "name": name}
    if model is not None:
        manifest["model"] = model
    if model_provider is not None:  # mirrors the builder: computed from the model, present for gpt-4o
        manifest["model_provider"] = model_provider
    if model_settings is not None:  # mirrors the builder: omitted when unset
        manifest["model_settings"] = model_settings

    # Prompts are ``{static: text}`` here; the dynamic-part shape is asserted by the builder-driven tests.
    if instructions:
        manifest["instructions"] = {"static": instructions}
    if system_prompts:
        manifest["system_prompts"] = {"static": system_prompts}

    if capabilities:
        manifest["capabilities"] = capabilities
    if handoffs:
        manifest["handoffs"] = handoffs
    if guardrails:
        manifest["guardrails"] = guardrails
    manifest["output_type"] = output_type if output_type is not None else dict(DEFAULT_OUTPUT_TYPE)

    # ``behaviors`` groups memory / tool-transforms / settings; ``settings`` is present for a normal agent.
    behaviors: dict = {}
    if memory:
        behaviors["memory"] = memory
    if tool_transforms:
        behaviors["tool_transforms"] = tool_transforms
    behaviors["settings"] = settings if settings is not None else dict(DEFAULT_SETTINGS)
    manifest["behaviors"] = behaviors

    if metadata is not None:  # display-only
        manifest["metadata"] = metadata
    return {"_dd": {"agent_manifest": manifest}}


def calculate_square_tool(x: int) -> int:
    """Calculates the square of a number"""
    return x * x


def foo_tool() -> str:
    """Return foo string"""
    return "foo"
