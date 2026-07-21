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


# The manifest is the LOCKED cross-framework shape, ADDITIVE over origin/main: the shipped keys
# ``framework`` / ``name`` / ``model`` / ``model_settings`` / ``instructions`` (str|None) /
# ``system_prompts`` (tuple) / ``tools`` (list) keep their exact name + type -- emitted ALWAYS, even
# when empty/None, exactly as main does. For a normal agent the builder also always emits the additive
# ``model_provider`` (computed from the model), ``output_type`` (defaulting to ``str``), and a flat
# ``settings`` block (``retries`` / ``tool_retries`` / ``end_strategy`` are present on every supported
# pydantic-ai version 0.8.1 / 1.0.0 / 1.63.0). Function tools appear in BOTH the flat ``tools`` list
# (registration order, back-compat) AND the unified ``capabilities`` superset (order-incidental ->
# sorted by ``(type, name)``). Dynamic prompt resolvers live in the separate ``extra_instructions``
# list (their descriptor shape is covered by the builder-driven ``test_manifest_*`` tests, so these
# end-to-end baselines carry only static text + function-tool capabilities). Per-test baselines.
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
    if model_provider is not None:  # additive; mirrors the builder -- computed from the model, present for gpt-4o
        manifest["model_provider"] = model_provider

    # FROZEN keys -- main emits each whenever its attr exists (i.e. always), even None/empty.
    manifest["model_settings"] = model_settings
    manifest["instructions"] = instructions
    manifest["system_prompts"] = (system_prompts,) if system_prompts else ()

    # ADDITIVE ordered list of dynamic prompt resolvers (present only when the agent has them).
    if extra_instructions:
        manifest["extra_instructions"] = extra_instructions

    manifest["tools"] = tools if tools is not None else []

    # Unified ``capabilities`` superset -- present only when non-empty. For these end-to-end baselines
    # the only capabilities are the agent's function tools, so auto-derive from ``tools`` (sorted by
    # ``(type, name)`` to match the builder's ``_sorted_definition_list``) unless overridden.
    if capabilities is None and tools:
        capabilities = sorted((_capability_from_tool(t) for t in tools), key=lambda c: (c["type"], c["name"]))
    if capabilities:
        manifest["capabilities"] = capabilities

    # ADDITIVE order-incidental lists -- present only when the agent actually has them.
    if handoffs:
        manifest["handoffs"] = handoffs
    if guardrails:
        manifest["guardrails"] = guardrails

    manifest["output_type"] = output_type if output_type is not None else dict(DEFAULT_OUTPUT_TYPE)

    # Nice-to-haves are flat (no ``behaviors`` grouping); ``settings`` is present for a normal agent.
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
