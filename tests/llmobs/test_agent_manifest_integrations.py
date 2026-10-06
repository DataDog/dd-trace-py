"""Agent manifest contract across integrations, exercised with stand-in framework objects.

Each builder reads attributes off a framework object, so a SimpleNamespace stands in for one and
these tests need no framework installed.
"""

import functools
import json
from types import SimpleNamespace
from typing import Optional
from unittest import mock

import pytest

from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._integrations.agent_manifest import build_agent_manifest
from ddtrace.llmobs._integrations.agent_manifest import filter_model_settings
from ddtrace.llmobs._integrations.agent_manifest import instruction_fields
from ddtrace.llmobs._integrations.agent_manifest import normalize_tool
from ddtrace.llmobs._integrations.claude_agent_sdk import ClaudeAgentSdkIntegration
from ddtrace.llmobs._integrations.crewai import CrewAIIntegration
from ddtrace.llmobs._integrations.google_adk import GoogleAdkIntegration
from ddtrace.llmobs._integrations.langgraph import LangGraphIntegration
from ddtrace.llmobs._integrations.openai_agents import OpenAIAgentsIntegration
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs.types import AgentManifest
from ddtrace.trace import Span


CANONICAL_KEYS = frozenset(AgentManifest.__annotations__)


_INVOCATIONS = []


def _never_called(*args, **kwargs):
    # Recorded rather than only raised, because a raising section is swallowed by the builder.
    _INVOCATIONS.append(args)
    raise AssertionError("a declared callable must not be invoked while building the manifest")


@pytest.fixture(autouse=True)
def _assert_no_declared_callable_invoked():
    _INVOCATIONS.clear()
    yield
    assert not _INVOCATIONS, "a declared callable was invoked while building the manifest"


def _search(query: str, limit: int = 5, tool_context=None) -> list:
    """Search the docs."""


class _Answer:
    pass


class ToolContext:
    """Named like ADK's, which ADK injects by annotation rather than by parameter name."""


class _Billing:
    def refund(self, order_id: str, reason: Optional[str] = None, ctx: ToolContext = None) -> str:
        """Refund an order."""


class MCPServerSse:
    def __init__(self, name):
        self.name = name


class _Graph:
    """Stands in for a compiled graph, which the integration keys weakly."""

    def __init__(self, name):
        self.name = name
        self.builder = None


def _manifest_from_span(span):
    meta = _get_llmobs_data_metastruct(span).get(LLMOBS_STRUCT.META, {})
    return meta.get(LLMOBS_STRUCT.METADATA, {}).get(LLMOBS_STRUCT.METADATA_DD, {}).get(LLMOBS_STRUCT.AGENT_MANIFEST)


def _adk_agent(**overrides):
    agent = dict(
        name="billing",
        description="Handles billing questions",
        model="gemini-2.0-flash",
        instruction=_never_called,
        global_instruction="Be polite.",
        static_instruction=None,
        tools=[_search],
        generate_content_config=SimpleNamespace(temperature=0.2, max_output_tokens=256),
        output_schema=_Answer,
        sub_agents=[SimpleNamespace(name="refunds", description="Issues refunds")],
        before_model_callback=None,
        before_tool_callback=None,
        model_config={"arbitrary_types_allowed": True, "extra": "forbid"},
    )
    agent.update(overrides)
    return SimpleNamespace(**agent)


def _openai_agent(**overrides):
    agent = dict(
        name="triage",
        instructions="Route the request.",
        prompt=None,
        handoff_description="Routes requests",
        model="gpt-4o",
        model_settings=SimpleNamespace(temperature=0.1, extra_headers={"Authorization": "Bearer secret"}),
        tools=[
            SimpleNamespace(
                name="lookup",
                description="Look up an order",
                params_json_schema={
                    "type": "object",
                    "properties": {"order_id": {"type": "string", "title": "Order Id"}},
                    "required": ["order_id"],
                },
            ),
            SimpleNamespace(
                name="hosted_mcp",
                tool_config={
                    "server_label": "github",
                    "server_url": "https://mcp.example.com?token=secret",
                    "headers": {"Authorization": "Bearer secret"},
                    "allowed_tools": ["search"],
                },
                on_approval_request=_never_called,
            ),
            SimpleNamespace(name="computer_use_preview", computer=object(), on_safety_check=_never_called),
        ],
        mcp_servers=[],
        output_type=None,
        handoffs=[SimpleNamespace(name="refunds", handoff_description="Issues refunds", tools=[], handoffs=[])],
        input_guardrails=[SimpleNamespace(name=None, guardrail_function=_never_called)],
        output_guardrails=[],
        tool_use_behavior="run_llm_again",
        reset_tool_choice=True,
    )
    agent.update(overrides)
    return SimpleNamespace(**agent)


def _crewai_agent(**overrides):
    agent = dict(
        role="AI Researcher",
        goal="Research AI",
        backstory="An expert in AI.",
        llm=SimpleNamespace(model="gpt-4o-mini", temperature=0.0, max_tokens=None),
        tools=[],
        allow_delegation=False,
        max_iter=25,
        allow_code_execution=False,
        code_execution_mode="safe",
    )
    agent.update(overrides)
    return SimpleNamespace(**agent)


def _claude_options(**overrides):
    options = dict(
        system_prompt="You are a code reviewer.",
        mcp_servers={"fs": {"command": "fs-server", "env": {"TOKEN": "secret"}}},
        agents={"tester": SimpleNamespace(description="Writes tests")},
        can_use_tool=_never_called,
        hooks=None,
        max_turns=3,
    )
    options.update(overrides)
    return SimpleNamespace(**options)


def _build(integration_name, *args):
    """Build a manifest the way each integration does on its agent span."""
    span = Span("agent")
    if integration_name == "google_adk":
        GoogleAdkIntegration(integration_config=mock.MagicMock())._tag_agent_manifest(span, {}, *args)
    elif integration_name == "openai_agents":
        integration = OpenAIAgentsIntegration(integration_config=mock.MagicMock())
        with mock.patch.object(
            OpenAIAgentsIntegration, "llmobs_enabled", new_callable=mock.PropertyMock, return_value=True
        ):
            integration._tag_agent_manifest_from_agent(span, *args)
    elif integration_name == "crewai":
        CrewAIIntegration(integration_config=mock.MagicMock())._tag_agent_manifest(span, *args)
    elif integration_name == "claude_agent_sdk":
        return ClaudeAgentSdkIntegration(integration_config=mock.MagicMock())._build_agent_manifest(*args)
    return _manifest_from_span(span)


CONTRACT_CASES = [
    ("google_adk", lambda: (_adk_agent(),)),
    ("openai_agents", lambda: (_openai_agent(),)),
    ("crewai", lambda: (_crewai_agent(),)),
    ("claude_agent_sdk", lambda: ("claude-sonnet", _claude_options(), {"tools": ["Read"], "mcp_servers": []})),
]


@pytest.mark.parametrize("integration_name,make_args", CONTRACT_CASES, ids=[c[0] for c in CONTRACT_CASES])
def test_manifest_contract(integration_name, make_args):
    manifest = _build(integration_name, *make_args())

    assert manifest, "the stand-in agent declares enough to report a manifest"
    assert set(manifest) <= CANONICAL_KEYS, set(manifest) - CANONICAL_KEYS
    assert manifest["framework"]
    # Round-trips through JSON unchanged, so nothing relies on the encoder's repr fallback.
    assert json.loads(json.dumps(manifest)) == manifest
    # Rebuilt from a fresh but identical declaration, so nothing per process (an address) or per run leaks in.
    assert _build(integration_name, *make_args()) == manifest


class TestSharedHelpers:
    def test_callable_instructions_ship_by_name(self):
        assert instruction_fields(_never_called) == {
            "extra_instructions": [{"type": "dynamic_instructions", "name": "_never_called"}]
        }
        assert instruction_fields("Be brief.") == {"instructions": "Be brief."}
        assert instruction_fields(object()) == {}

    def test_model_settings_allowlist(self):
        assert filter_model_settings(
            {"temperature": 0, "extra_headers": {"Authorization": "x"}, "extra_body": {"a": 1}}
        ) == {"temperature": 0}
        assert filter_model_settings(None) == {}

    def test_normalize_tool_flattens_json_schema(self):
        assert normalize_tool(
            "add", "Adds", {"type": "object", "properties": {"a": {"type": "integer", "title": "A"}}, "required": ["a"]}
        ) == {"name": "add", "description": "Adds", "parameters": {"a": {"type": "integer", "required": True}}}
        assert normalize_tool("", "unnamed") is None
        assert normalize_tool("t", object())["description"] == ""

    def test_optional_parameters_keep_their_type(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"anyOf": [{"type": "integer"}, {"type": "null"}], "default": None},
                "tags": {"type": ["array", "null"]},
                "item": {"$ref": "#/$defs/Item"},
                "extra": {"default": None},
            },
            "required": ["query"],
        }
        manifest = build_agent_manifest(
            "X", None, (("tools", lambda _: {"tools": [normalize_tool("t", None, schema)]}),), "test"
        )
        assert manifest["tools"][0]["parameters"] == {
            "query": {"type": "string", "required": True},
            "limit": {"type": "integer"},
            "tags": {"type": "array"},
            "item": {"type": "Item"},
            "extra": {"type": "any"},
        }

    def test_failing_section_costs_only_its_fields(self):
        def broken(_):
            raise RuntimeError("framework changed")

        manifest = build_agent_manifest("X", None, (("labels", lambda _: {"name": "a"}), ("tools", broken)), "test")
        assert manifest == {"name": "a", "framework": "X"}

    def test_empty_manifest_is_not_reported(self):
        assert build_agent_manifest("X", None, (("labels", lambda _: {"name": ""}),), "test") == {}


class TestGoogleAdk:
    def test_manifest(self):
        assert _build("google_adk", _adk_agent()) == {
            "framework": "Google ADK",
            "name": "billing",
            "handoff_description": "Handles billing questions",
            "extra_instructions": [{"type": "dynamic_instructions", "name": "_never_called"}],
            "system_prompts": ["Be polite."],
            "model": "gemini-2.0-flash",
            "model_settings": {"temperature": 0.2, "max_tokens": 256},
            "tools": [
                {
                    "name": "_search",
                    "description": "Search the docs.",
                    "parameters": {"query": {"type": "string", "required": True}, "limit": {"type": "integer"}},
                }
            ],
            "data_contracts": {"output": {"name": "_Answer"}},
            "handoffs": [{"agent_name": "refunds", "handoff_description": "Issues refunds"}],
        }

    def test_model_object(self):
        manifest = _build("google_adk", _adk_agent(model=SimpleNamespace(model="gemini-2.5-pro")))
        assert manifest["model"] == "gemini-2.5-pro"

    def test_static_instruction_content(self):
        content = SimpleNamespace(parts=[SimpleNamespace(text="Cached preamble.")])
        manifest = _build("google_adk", _adk_agent(global_instruction="", static_instruction=content))
        assert manifest["system_prompts"] == ["Cached preamble."]

    def test_callable_tools(self):
        billing = _Billing()
        tools = [billing.refund, functools.partial(_search, limit=3)]
        manifest = _build("google_adk", _adk_agent(tools=tools))
        assert manifest["tools"] == [
            {
                "name": "refund",
                "description": "Refund an order.",
                "parameters": {"order_id": {"type": "string", "required": True}, "reason": {"type": "string"}},
            },
            {
                "name": "_search",
                "description": "Search the docs.",
                "parameters": {"query": {"type": "string", "required": True}, "limit": {"type": "integer"}},
            },
        ]


class TestOpenAIAgents:
    def test_manifest(self):
        assert _build("openai_agents", _openai_agent()) == {
            "framework": "OpenAI",
            "name": "triage",
            "handoff_description": "Routes requests",
            "instructions": "Route the request.",
            "model": "gpt-4o",
            "model_settings": {"temperature": 0.1},
            "tools": [
                {
                    "name": "lookup",
                    "description": "Look up an order",
                    "parameters": {"order_id": {"type": "string", "required": True}},
                },
                {"name": "hosted_mcp", "server_label": "github", "allowed_tools": ["search"]},
                {"name": "computer_use_preview"},
            ],
            "handoffs": [{"agent_name": "refunds", "handoff_description": "Issues refunds"}],
            "guardrails": ["_never_called"],
            "agent_settings": {"tool_use_behavior": "run_llm_again", "reset_tool_choice": True},
        }

    def test_callable_instructions_and_stored_prompt(self):
        manifest = _build("openai_agents", _openai_agent(instructions=_never_called, prompt={"id": "pmpt_123"}))
        assert "instructions" not in manifest
        assert manifest["extra_instructions"] == [
            {"type": "dynamic_instructions", "name": "_never_called"},
            {"type": "prompt", "name": "pmpt_123"},
        ]

    def test_mcp_server_default_name_is_not_reported(self):
        servers = [MCPServerSse("sse: https://mcp.example.com/sse?token=secret"), MCPServerSse("github")]
        manifest = _build("openai_agents", _openai_agent(mcp_servers=servers))
        assert manifest["capabilities"] == [
            {"name": "MCPServerSse", "type": "mcp"},
            {"name": "github", "type": "mcp"},
        ]

    def test_web_search_user_location_is_not_reported(self):
        tool = SimpleNamespace(
            name="web_search", user_location={"city": "New York"}, search_context_size="low", filters=None
        )
        manifest = _build("openai_agents", _openai_agent(tools=[tool]))
        assert manifest["tools"] == [{"name": "web_search", "search_context_size": "low"}]


class TestCrewAI:
    def test_manifest(self):
        assert _build("crewai", _crewai_agent()) == {
            "framework": "CrewAI",
            "name": "AI Researcher",
            "instructions": "Research AI\n\nAn expert in AI.",
            "model": "gpt-4o-mini",
            "model_settings": {"temperature": 0.0},
            "handoffs": {"allow_delegation": False},
            "agent_settings": {"max_iter": 25},
        }

    def test_code_execution_settings(self):
        manifest = _build("crewai", _crewai_agent(allow_code_execution=True, code_execution_mode="unsafe"))
        assert manifest["agent_settings"] == {
            "max_iter": 25,
            "allow_code_execution": True,
            "code_execution_mode": "unsafe",
        }

    def test_declared_goal_is_preferred_over_interpolated(self):
        agent = _crewai_agent(_original_goal="Research {topic}", _original_backstory="An expert in {topic}.")
        assert _build("crewai", agent)["instructions"] == "Research {topic}\n\nAn expert in {topic}."


class TestLangGraph:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("gpt-4o", {"model": "gpt-4o"}),
            ("openai:gpt-4o", {"model": "gpt-4o", "model_provider": "openai"}),
        ],
    )
    def test_react_agent_model_string(self, model, expected):
        integration = LangGraphIntegration(integration_config=mock.MagicMock())
        agent = _Graph("react")
        with mock.patch.object(
            LangGraphIntegration, "llmobs_enabled", new_callable=mock.PropertyMock, return_value=True
        ):
            integration.llmobs_handle_agent_manifest(agent, (model, []), {"name": "react", "prompt": _never_called})
        manifest = integration._get_agent_manifest(agent, (), {})
        assert manifest == {
            "framework": "LangGraph",
            "name": "react",
            "extra_instructions": [{"type": "dynamic_prompt", "name": "_never_called"}],
            **expected,
        }

    def test_run_config_does_not_leak_into_cache(self):
        integration = LangGraphIntegration(integration_config=mock.MagicMock())
        graph = _Graph("graph")
        first = integration._get_agent_manifest(graph, (), {"recursion_limit": 100})
        second = integration._get_agent_manifest(graph, (), {})
        assert first["agent_settings"] == {"recursion_limit": 100}
        assert second == {"framework": "LangGraph", "name": "graph"}

    def test_tool_parameters_exclude_injected_args(self):
        tool = SimpleNamespace(
            name="transfer",
            description="Hand off",
            tool_call_schema={"type": "object", "properties": {"to": {"type": "string"}}, "required": ["to"]},
            args_schema={
                "type": "object",
                "properties": {"to": {"type": "string"}, "state": {"type": "object"}},
                "required": ["to", "state"],
            },
        )
        integration = LangGraphIntegration(integration_config=mock.MagicMock())
        agent = _Graph("supervisor")
        with mock.patch.object(
            LangGraphIntegration, "llmobs_enabled", new_callable=mock.PropertyMock, return_value=True
        ):
            integration.llmobs_handle_agent_manifest(agent, ("gpt-4o", [tool]), {})
        assert integration._get_agent_manifest(agent, (), {})["tools"] == [
            {"name": "transfer", "description": "Hand off", "parameters": {"to": {"type": "string", "required": True}}}
        ]

    def test_stop_sequences_field_is_read(self):
        model = SimpleNamespace(model_name="claude-sonnet", stop_sequences=["\n\nHuman:"])
        integration = LangGraphIntegration(integration_config=mock.MagicMock())
        agent = _Graph("react")
        with mock.patch.object(
            LangGraphIntegration, "llmobs_enabled", new_callable=mock.PropertyMock, return_value=True
        ):
            integration.llmobs_handle_agent_manifest(agent, (model, []), {})
        assert integration._get_agent_manifest(agent, (), {})["model_settings"] == {"stop_sequences": ["\n\nHuman:"]}

    def test_declared_recursion_limit_wins_over_run_config(self):
        integration = LangGraphIntegration(integration_config=mock.MagicMock())
        graph = _Graph("graph")
        graph.config = {"recursion_limit": 50}
        manifest = integration._get_agent_manifest(graph, (), {"recursion_limit": 100})
        assert manifest["agent_settings"] == {"recursion_limit": 50}


class TestClaudeAgentSdk:
    def test_manifest(self):
        manifest = _build(
            "claude_agent_sdk",
            "claude-sonnet",
            _claude_options(),
            {"tools": ["Read", "Bash"], "mcp_servers": [{"name": "fs", "status": "connected"}]},
        )
        assert manifest == {
            "framework": "Claude Agent SDK",
            "model": "claude-sonnet",
            "instructions": "You are a code reviewer.",
            "tools": [{"name": "Read"}, {"name": "Bash"}],
            "capabilities": [{"name": "fs", "type": "mcp"}],
            "handoffs": [{"agent_name": "tester", "handoff_description": "Writes tests"}],
            "guardrails": ["_never_called"],
            "agent_settings": {"max_turns": 3},
        }

    def test_preset_system_prompt(self):
        options = _claude_options(system_prompt={"type": "preset", "preset": "claude_code", "append": "Be terse."})
        manifest = _build("claude_agent_sdk", "claude-sonnet", options, {})
        assert manifest["instructions"] == "Be terse."
        assert manifest["extra_instructions"] == [{"type": "preset", "name": "claude_code"}]

    def test_empty_options_servers_fall_back_to_init(self):
        options = _claude_options(mcp_servers={})
        init = {"tools": ["Read"], "mcp_servers": [{"name": "plugin-fs", "status": "connected"}]}
        manifest = _build("claude_agent_sdk", "claude-sonnet", options, init)
        assert manifest["capabilities"] == [{"name": "plugin-fs", "type": "mcp"}]
