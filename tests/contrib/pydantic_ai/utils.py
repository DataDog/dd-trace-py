import mock
import pytest

from ddtrace.llmobs._utils import _get_llmobs_data_metastruct


PYDANTIC_AI_TAGS = {
    "ml_app": "<ml-app-name>",
    "service": "tests.contrib.pydantic_ai",
    "integration": "pydantic_ai",
}


# pydantic-ai's own defaults for an agent that configures none of these, at the versions suitespec
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


class _UnserializableSentinel:
    """Stand-in for provider sentinels such as OpenAI's ``Omit`` / ``NOT_GIVEN``."""

    def __repr__(self):
        return "Omit()"


def _test_model():
    """A model that synthesises schema-valid output, needed when output_type is a function."""
    from pydantic_ai.models.test import TestModel

    return TestModel()


def _function_model():
    """A model that answers locally, so a manifest test needs no cassette and no network."""
    from pydantic_ai.messages import ModelResponse
    from pydantic_ai.messages import TextPart
    from pydantic_ai.models.function import FunctionModel

    def model_func(messages, info):
        return ModelResponse(parts=[TextPart(content="Hello!")])

    return FunctionModel(model_func)


def _manifest_of(span):
    return _get_llmobs_data_metastruct(span)["meta"]["metadata"]["_dd"]["agent_manifest"]


class ABSENT:
    """Marks keys a case asserts are missing. An empty expected dict asserts nothing at all."""

    def __init__(self, *keys):
        self.keys = keys


def _assert_contains(manifest, expected, path=""):
    """Assert every field in expected matches, ignoring anything not mentioned.

    Lets a case skip builtin_tools' name, which is "WebSearchTool" below 1.63.0, "web_search" after.
    """
    for key, want in expected.items():
        assert key in manifest, "manifest is missing {}{}".format(path, key)
        got = manifest[key]
        if isinstance(want, dict) and isinstance(got, dict):
            _assert_contains(got, want, "{}{}.".format(path, key))
        elif isinstance(want, list) and isinstance(got, list):
            assert len(got) == len(want), "{}{}: expected {} entries, got {}".format(path, key, len(want), len(got))
            for index, (want_entry, got_entry) in enumerate(zip(want, got)):
                if isinstance(want_entry, dict) and isinstance(got_entry, dict):
                    _assert_contains(got_entry, want_entry, "{}{}[{}].".format(path, key, index))
                else:
                    assert got_entry == want_entry, "{}{}[{}]".format(path, key, index)
        else:
            assert got == want, "{}{}: expected {!r}, got {!r}".format(path, key, want, got)


# What must never reach the wire, one case per carrier: (kwargs factory, forbidden substrings,
# manifest subset that must still be present, minimum pydantic-ai version). Collected in one table so
# the security contract is reviewable in a single place.
MANIFEST_LEAK_CASES = [
    pytest.param(
        lambda: dict(
            model_settings={
                "temperature": 0.5,
                "extra_headers": {"Authorization": "Bearer sk-leak-canary"},
                "extra_body": {"credential": "sk-leak-canary-2"},
                "openai_user": "end-user-4711",
            }
        ),
        ["sk-leak-canary", "Authorization", "extra_headers", "extra_body", "end-user-4711"],
        {"model_settings": {"temperature": 0.5}},
        None,
        id="transport_params_never_ship",
    ),
    pytest.param(
        lambda: dict(
            model_settings={
                "temperature": 0.5,
                "anthropic_metadata": {"user_id": "user-42-pii"},
                "bedrock_request_metadata": {"trace_token": "sk-leak-canary"},
                "openai_user": "end-user-99",
            }
        ),
        ["sk-leak-canary", "user-42-pii", "end-user-99"],
        {"model_settings": {"temperature": 0.5}},
        None,
        id="provider_blobs_never_ship",
    ),
    pytest.param(
        # The shared schema has no field for validation_context, so it drops entirely rather than
        # shipping key names. It accepts Any, and a dict there routinely holds a live client or a key.
        lambda: dict(validation_context={"tenant": "acme", "api_key": "sk-leak-canary"}),
        ["sk-leak-canary", "validation_context"],
        {},
        (1, 63, 0),
        id="validation_context_never_ships",
    ),
]


class _Deps:
    tenant: str


def _redact_history(messages):
    """Strip personal data from history."""
    return messages


def _tenant_toolset(ctx):
    """Load the tenant's toolset."""
    return None


def _escalate(reason: str) -> str:
    """Hand the ticket to a human."""
    return reason


def _builtin_web_search():
    from pydantic_ai.builtin_tools import WebSearchTool

    return WebSearchTool(search_context_size="high", max_uses=3)


# One field mapping per case: (kwargs factory, expected manifest subset, minimum pydantic-ai version).
# A factory rather than a literal so version-gated imports happen only when the case runs.
MANIFEST_FIELD_CASES = [
    pytest.param(
        lambda: dict(model_settings={"temperature": 0, "parallel_tool_calls": False, "max_tokens": 0}),
        # Falsy is not absent. Filtering on truthiness is what loses a deliberate temperature of 0.
        {"model_settings": {"temperature": 0, "parallel_tool_calls": False, "max_tokens": 0}},
        None,
        id="falsy_model_params_survive",
    ),
    pytest.param(
        lambda: dict(model_settings={"temperature": 0.5, "stop_sequences": ["END"], "timeout": 30.0}),
        {"model_settings": {"temperature": 0.5, "stop_sequences": ["END"], "timeout": 30.0}},
        None,
        id="allowlisted_params_pass_through_unrenamed",
    ),
    pytest.param(
        # A provider-prefixed key is not on the allowlist, so it drops rather than being promoted.
        lambda: dict(model_settings={"openai_reasoning_effort": "high", "temperature": 0.5}),
        {"model_settings": {"temperature": 0.5}},
        None,
        id="provider_prefixed_param_drops",
    ),
    pytest.param(
        lambda: dict(history_processors=[_redact_history]),
        {"memory_policies": ["_redact_history"]},
        None,
        id="history_processors_land_in_memory_policies",
    ),
    pytest.param(
        # A processor listed twice runs twice, so it is reported twice: collapsing the repeat would
        # describe a pipeline the agent does not run, the same way reordering it would.
        lambda: dict(history_processors=[_redact_history, _redact_history]),
        {"memory_policies": ["_redact_history", "_redact_history"]},
        None,
        id="repeated_history_processor_is_reported_twice",
    ),
    pytest.param(
        lambda: dict(toolsets=[_tenant_toolset]),
        {"capabilities": [{"name": "_tenant_toolset", "type": "custom"}]},
        (0, 4, 4),
        id="dynamic_toolset_is_a_custom_capability",
    ),
    pytest.param(
        lambda: dict(builtin_tools=[_builtin_web_search()]),
        {"capabilities": [{"name": mock.ANY, "type": "builtin"}]},
        None,
        id="builtin_tool_is_a_capability",
    ),
    pytest.param(
        lambda: dict(tool_timeout=12.5, max_concurrency=4),
        {"agent_settings": {"tool_timeout": 12.5, "max_concurrency": 4}},
        (1, 63, 0),
        id="tool_timeout_and_max_concurrency",
    ),
    pytest.param(
        lambda: dict(metadata={"suite": "manifest", "owner": "llmobs"}),
        {"metadata": {"suite": "manifest", "owner": "llmobs"}},
        (1, 63, 0),
        id="metadata_is_top_level",
    ),
    pytest.param(
        lambda: dict(deps_type=_Deps, end_strategy="exhaustive"),
        {"agent_settings": {"deps_type": "_Deps", "end_strategy": "exhaustive"}},
        None,
        id="deps_type_and_end_strategy_land_in_agent_settings",
    ),
    pytest.param(
        lambda: dict(model=_test_model(), output_type=[_escalate]),
        ABSENT("handoffs", "data_contracts"),
        None,
        id="output_function_does_not_become_a_handoff",
    ),
]
