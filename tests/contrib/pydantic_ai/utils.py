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


# The manifest is ADDITIVE over origin/main: the shipped keys ``framework`` / ``name`` / ``model`` /
# ``model_settings`` / ``instructions`` (str|None) / ``system_prompts`` (tuple) / ``tools`` (list) keep their
# exact name + type -- emitted ALWAYS, even when empty/None, exactly as main does. For a normal agent the
# builder also always emits the additive ``model_provider`` (computed from the model), ``output_type``
# (defaulting to ``str``), and a flat ``settings`` block (``retries`` / ``tool_retries`` / ``end_strategy``
# are present on every supported pydantic-ai version 0.8.1 / 1.0.0 / 1.63.0). Prompts are flat: static text
# in ``instructions`` / ``system_prompts``, runtime resolvers in the separate ``dynamic_instructions`` /
# ``dynamic_system_prompts`` lists (their descriptor shape is covered by the builder-driven ``test_manifest_*``
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
    system_prompts=None,  # a single static system-prompt string (or None)
    tools=None,
    dynamic_instructions=None,
    dynamic_system_prompts=None,
    sub_agents=None,
    mcp_servers=None,
    builtin_tools=None,
    custom_toolsets=None,
    handoffs=None,
    guardrails=None,
    output_type=None,
    settings=None,
    history_processors=None,
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
    manifest["tools"] = tools if tools is not None else []

    # ADDITIVE lists -- present only when the agent actually has them.
    for key, val in (
        ("dynamic_instructions", dynamic_instructions),
        ("dynamic_system_prompts", dynamic_system_prompts),
        ("sub_agents", sub_agents),
        ("mcp_servers", mcp_servers),
        ("builtin_tools", builtin_tools),
        ("custom_toolsets", custom_toolsets),
        ("handoffs", handoffs),
        ("guardrails", guardrails),
    ):
        if val:
            manifest[key] = val

    manifest["output_type"] = output_type if output_type is not None else dict(DEFAULT_OUTPUT_TYPE)

    # Nice-to-haves are flat (no ``behaviors`` grouping); ``settings`` is present for a normal agent.
    if history_processors:
        manifest["history_processors"] = history_processors
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
