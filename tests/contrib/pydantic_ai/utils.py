import re

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.llmobs._utils import assert_llmobs_span_data


PYDANTIC_AI_TAGS = {
    "ml_app": "<ml-app-name>",
    "service": "tests.contrib.pydantic_ai",
    "integration": "pydantic_ai",
}

TOOL_DESCRIPTION_METADATA = {"description": "Calculates the square of a number"}

_SOURCE_HASH_RE = re.compile(r"[0-9a-f]{64}")


def assert_source_hash(descriptor):
    """A function descriptor carries a sha256 hex digest of its source, never the source itself."""
    assert _SOURCE_HASH_RE.fullmatch(descriptor["source_hash"]), descriptor
    assert "source" not in descriptor, descriptor


def extract_extra_instruction(manifest, entry_type):
    """Return the single extra_instructions descriptor of ``entry_type``; assert exactly one exists."""
    entries = [e for e in manifest["extra_instructions"] if e["type"] == entry_type]
    assert len(entries) == 1, entries
    return entries[0]["content"]


def pop_single_agent_span(test_spans):
    """Assert the trace is a single agent span and return its LLMObs meta_struct data."""
    spans = [s for trace in test_spans.pop_traces() for s in trace]
    assert len(spans) == 1, spans
    return _get_llmobs_data_metastruct(spans[0])


def assert_single_agent_span(test_spans, *, name="test_agent", **kwargs):
    """Assert the trace is a single agent span carrying the given fields; span_kind/tags are fixed."""
    assert_llmobs_span_data(
        pop_single_agent_span(test_spans), span_kind="agent", name=name, tags=PYDANTIC_AI_TAGS, **kwargs
    )


def pop_agent_and_tool_spans(test_spans):
    """Assert the trace is [agent, tool] and return (agent_data, tool_data, agent_span_id)."""
    trace = test_spans.pop_traces()[0]
    return _get_llmobs_data_metastruct(trace[0]), _get_llmobs_data_metastruct(trace[1]), str(trace[0].span_id)


def assert_calculate_square_tool_span(tool_span_data, parent_id):
    """Assert the calculate_square_tool tool span (square of 2 -> 4) parented at ``parent_id``."""
    assert_llmobs_span_data(
        tool_span_data,
        span_kind="tool",
        name="calculate_square_tool",
        parent_id=parent_id,
        input_value='{"x":2}',
        output_value="4",
        metadata=TOOL_DESCRIPTION_METADATA,
        tags=PYDANTIC_AI_TAGS,
    )


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
    extra_instructions=None,
    handoffs=None,
    guardrails=None,
    output_type=None,
    agent_settings=None,
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

    # Auto-derive capabilities from the function tools, sorted by (type, name) to match the builder.
    if tools:
        manifest["capabilities"] = sorted(
            (_capability_from_tool(t) for t in tools), key=lambda c: (c["type"], c["name"])
        )

    if handoffs:
        manifest["handoffs"] = handoffs
    if guardrails:
        manifest["guardrails"] = guardrails

    manifest["output_type"] = output_type if output_type is not None else dict(DEFAULT_OUTPUT_TYPE)

    if memory_policies:
        manifest["memory_policies"] = memory_policies
    if tool_transforms:
        manifest["tool_transforms"] = tool_transforms
    manifest["agent_settings"] = agent_settings if agent_settings is not None else dict(DEFAULT_SETTINGS)

    if metadata is not None:  # display-only
        manifest["metadata"] = metadata
    return {"_dd": {"agent_manifest": manifest}}


def calculate_square_tool(x: int) -> int:
    """Calculates the square of a number"""
    return x * x


def foo_tool() -> str:
    """Return foo string"""
    return "foo"
