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


# Expected ``_dd.agent_manifest`` for a given agent shape, mirroring the builder's output. Keys the
# builder always emits are always set here; the rest are included only when non-empty.
DEFAULT_OUTPUT_TYPE = {"name": "str"}
DEFAULT_SETTINGS = {"retries": 1, "tool_retries": 1, "end_strategy": "early"}


def _capability_from_tool(tool: dict) -> dict:
    """Mirror the builder's ``tool`` -> ``capabilities`` envelope entry for a flat function tool."""
    cap = {"name": tool["name"], "type": "tool", "content": {"schema": tool.get("parameters", {})}}
    if tool.get("description") is not None:
        cap["description"] = tool["description"]
    return cap


def expected_agent_metadata(
    *,
    name="test_agent",
    model="gpt-4o",
    model_provider="openai",
    model_settings=None,
    instructions=None,
    system_prompts=None,  # a single static system-prompt string (or None)
    tools=None,
    capabilities=None,  # explicit override; otherwise auto-derived from ``tools``
    extra_instructions=None,
    handoffs=None,
    guardrails=None,
    output_type=None,
    settings=None,
    memory_policies=None,
    tool_transforms=None,
    metadata=None,
) -> dict:
    manifest: dict = {"framework": "PydanticAI", "name": name}
    if model is not None:
        manifest["model"] = model
    if model_provider is not None:
        manifest["model_provider"] = model_provider

    # Always set, even when None/empty.
    manifest["model_settings"] = model_settings
    manifest["instructions"] = instructions
    manifest["system_prompts"] = (system_prompts,) if system_prompts else ()

    if extra_instructions:
        manifest["extra_instructions"] = extra_instructions

    manifest["tools"] = tools if tools is not None else []

    # Auto-derive capabilities from the function tools (sorted by (type, name) to match the builder)
    # unless the caller passes them explicitly.
    if capabilities is None and tools:
        capabilities = sorted((_capability_from_tool(t) for t in tools), key=lambda c: (c["type"], c["name"]))
    if capabilities:
        manifest["capabilities"] = capabilities

    if handoffs:
        manifest["handoffs"] = handoffs
    if guardrails:
        manifest["guardrails"] = guardrails

    manifest["output_type"] = output_type if output_type is not None else dict(DEFAULT_OUTPUT_TYPE)

    if memory_policies:
        manifest["memory_policies"] = memory_policies
    if tool_transforms:
        manifest["tool_transforms"] = tool_transforms
    manifest["settings"] = settings if settings is not None else dict(DEFAULT_SETTINGS)

    if metadata is not None:  # display-only
        manifest["metadata"] = metadata
    return {"_dd": {"agent_manifest": manifest}}


def calculate_square_tool(x: int) -> int:
    """Calculates the square of a number"""
    return x * x


def foo_tool() -> str:
    """Return foo string"""
    return "foo"
