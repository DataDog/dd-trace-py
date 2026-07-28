import hashlib
import inspect
import json

import mock
import pydantic_ai
import pytest
from typing_extensions import TypedDict

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import load_data_value
from ddtrace.llmobs._utils import safe_json
from tests.contrib.pydantic_ai.utils import PYDANTIC_AI_TAGS
from tests.contrib.pydantic_ai.utils import assert_calculate_square_tool_span
from tests.contrib.pydantic_ai.utils import assert_single_agent_span
from tests.contrib.pydantic_ai.utils import assert_source_hash
from tests.contrib.pydantic_ai.utils import calculate_square_tool
from tests.contrib.pydantic_ai.utils import expected_agent_metadata
from tests.contrib.pydantic_ai.utils import expected_calculate_square_tool
from tests.contrib.pydantic_ai.utils import expected_foo_tool
from tests.contrib.pydantic_ai.utils import extract_extra_instruction
from tests.contrib.pydantic_ai.utils import foo_tool
from tests.contrib.pydantic_ai.utils import pop_agent_and_tool_spans
from tests.contrib.pydantic_ai.utils import pop_single_agent_span
from tests.llmobs._utils import assert_llmobs_span_data


PYDANTIC_AI_VERSION = parse_version(pydantic_ai.__version__)


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_enabled=True, _llmobs_ml_app="<ml-app-name>")],
)
class TestLLMObsPydanticAI:
    async def test_agent_run(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        model_settings = {"max_tokens": 100, "temperature": 0.5}
        instructions = "dummy instructions"
        system_prompt = "dummy system prompt"
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o",
                name="test_agent",
                instructions=instructions,
                system_prompt=system_prompt,
                tools=[calculate_square_tool],
                model_settings=model_settings,
            )
            result = await agent.run("Hello, world!")
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(
                instructions=instructions,
                model_settings=model_settings,
                tools=expected_calculate_square_tool(),
                system_prompts=system_prompt,
            ),
        )

    def test_agent_run_sync(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = agent.run_sync("Hello, world!")
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    async def test_agent_run_with_metadata(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """An agent's statically-configured ``metadata`` dict is captured into the agent manifest."""
        agent_metadata = {"version": "v2", "team": "billing"}
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata=agent_metadata)
            result = await agent.run("Hello, world!")
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(metadata=agent_metadata),
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    async def test_agent_run_with_callable_metadata_not_captured(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Callable ``metadata`` is not statically serializable, so it is omitted from the manifest."""
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata=lambda ctx: {"version": "dyn"})
            result = await agent.run("Hello, world!")
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    def test_manifest_metadata_is_deep_copied_not_aliased(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """``metadata`` is a deep copy of ``agent._metadata``, not the live reference: the agent is reused and the event
        dict is buffered until flush, so a shared (even shallow-copied) nested container would let a later mutation
        rewrite a queued span. Fails if it stores ``_metadata`` directly or shallow-copied.
        """
        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", metadata={"team": "billing", "nested": {"k": "RUN1"}}
        )

        manifest = integration._build_agent_manifest(agent)
        assert manifest["metadata"] == {"team": "billing", "nested": {"k": "RUN1"}}
        assert manifest["metadata"] is not agent._metadata
        assert manifest["metadata"]["nested"] is not agent._metadata["nested"]

        # A NESTED mutation after the manifest is built must not leak into the captured copy; a
        # shallow copy would share the inner dict and fail this assertion.
        agent._metadata["nested"]["k"] = "RUN2"
        agent._metadata["new_key"] = "leaked"
        assert manifest["metadata"]["nested"]["k"] == "RUN1"
        assert manifest["metadata"] == {"team": "billing", "nested": {"k": "RUN1"}}

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    def test_manifest_metadata_unserializable_is_omitted(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """``metadata`` that ``safe_json`` cannot serialize is omitted, not stored (an unserializable dict would make
        ``_writer.enqueue`` raise and silently drop the span). Fails if a ``None`` serialization reaches the store
        branch.
        """

        poison_metadata = {"x": {1: "a", "b": 2}}
        assert safe_json(poison_metadata) is None  # precondition: this dict is not JSON-serializable
        manifest = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata=poison_metadata)
        )
        assert "metadata" not in manifest

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    def test_manifest_empty_metadata_omitted(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """An empty or unset ``metadata`` dict yields no ``metadata`` key."""

        empty_manifest = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata={})
        )
        assert "metadata" not in empty_manifest

        default_manifest = integration._build_agent_manifest(pydantic_ai.Agent(model="gpt-4o", name="test_agent"))
        assert "metadata" not in default_manifest

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="agent attrs verified on pydantic-ai >=1.63.0")
    async def test_agent_run_with_deps_type_in_agent_settings(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """The dependency-injection deps type is captured into the flat ``manifest.agent_settings.deps_type``."""

        class SupportDeps:
            pass

        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", deps_type=SupportDeps)
            result = await agent.run("Hello, world!")
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(
                agent_settings={"retries": 1, "tool_retries": 1, "end_strategy": "early", "deps_type": "SupportDeps"},
            ),
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="builtin tool kinds verified on pydantic-ai >=1.63.0")
    def test_agent_run_with_builtin_capability(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """A builtin tool is captured as a typed ``builtin`` capability, read from ``_builtin_tools`` on the constructed
        agent (no run needed).
        """
        from pydantic_ai.builtin_tools import WebSearchTool

        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", builtin_tools=[WebSearchTool()])
        capabilities = integration._build_capabilities(agent)
        builtins = [c["name"] for c in capabilities if c["type"] == "builtin"]
        assert builtins == ["web_search"], capabilities

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output_type/markers verified on pydantic-ai >=1.63.0")
    def test_agent_run_with_structured_output_type(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """A Pydantic ``BaseModel`` output type is captured as ``output_type`` with a JSON schema, read from
        ``agent.output_type`` (no run needed).
        """
        from pydantic import BaseModel

        class Weather(BaseModel):
            city: str
            temperature: int

        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=Weather)
        output_type = integration._get_agent_output_type(agent)
        assert output_type["name"] == "Weather"
        assert output_type["schema"]["properties"].keys() == {"city", "temperature"}
        assert output_type["schema"]["required"] == ["city", "temperature"]
        # callables are NOT captured here, so a structured-only agent has no handoffs
        assert integration._get_agent_handoffs(agent) == []

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output_type verified on pydantic-ai >=1.63.0")
    def test_manifest_output_type_union_captures_all_alternatives(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """A multi-output union captures ALL alternatives: ``name`` joins the members and ``schema`` is the union
        ``anyOf``, so any alternative change is reflected. Fails-on-revert if it collapses to the first model.
        """
        from pydantic import BaseModel

        class Fruit(BaseModel):
            kind: str

        class Vehicle(BaseModel):
            wheels: int

        union = integration._get_agent_output_type(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=[Fruit, Vehicle])
        )
        assert union["name"] == "Fruit | Vehicle"
        blob = safe_json(union["schema"])
        assert "Fruit" in blob and "Vehicle" in blob
        single = integration._get_agent_output_type(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=Fruit)
        )
        assert union != single  # regression: [Fruit, Vehicle] no longer collapses to Fruit
        # A scalar + model union keeps both members (previously the scalar was dropped).
        mixed = integration._get_agent_output_type(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=[str, Fruit])
        )
        assert mixed["name"] == "str | Fruit"

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="MCP toolsets verified on pydantic-ai >=1.63.0")
    def test_manifest_mcp_capability(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """An MCP toolset is captured as a typed ``mcp`` capability: an HTTP server carries a scrubbed ``scheme://host``
        uri, a stdio server carries none. Builder-driven; skipped when the ``mcp`` extra is absent.
        """
        mcp = pytest.importorskip("pydantic_ai.mcp")

        stdio_server = mcp.MCPServerStdio(command="echo", args=["hi"], id="stdio-mcp")
        http_server = mcp.MCPServerStreamableHTTP(url="https://example.com/mcp", id="http-mcp")
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", toolsets=[stdio_server, http_server])
        assert integration._get_mcp_servers(agent) == [
            {"name": "stdio-mcp"},
            {"name": "http-mcp", "uri": "https://example.com"},
        ]

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="MCP toolsets verified on pydantic-ai >=1.63.0")
    def test_manifest_mcp_uri_is_redacted(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """Regression: the MCP ``uri`` is an allowlist of ``scheme://host[:port]`` only, dropping
        userinfo/path/query/fragment secrets; a stdio command emits no uri. Fails if the redactor reverts to a query-
        key denylist or keeps path/userinfo.
        """
        mcp = pytest.importorskip("pydantic_ai.mcp")

        # Userinfo + secret-in-path + secrets under both a non-allowlist (``pwd``, ``x-api-key``) and
        # an allowlist (``api_key``) query key + fragment.
        http_server = mcp.MCPServerStreamableHTTP(
            url="https://user:tok@host.example.com/mcp/sk-PATHSECRET/stream"
            "?pwd=SECRET1&x-api-key=SECRET2&api_key=sk-secret&foo=bar#token=abc",
            id="http-mcp",
        )
        stdio_server = mcp.MCPServerStdio(
            command="/usr/local/bin/secret-server", args=["--token", "sk-xyz", "--db", "/etc/creds/db"], id="stdio-mcp"
        )
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", toolsets=[http_server, stdio_server])
        servers = integration._get_mcp_servers(agent)
        assert len(servers) == 2
        http_cap, stdio_cap = servers
        http_uri = http_cap["uri"]
        # No credential material survives: userinfo, path secret, NON-allowlist query keys
        # (``pwd``/``x-api-key``), allowlist query key, and fragment are all dropped.
        for secret in ("user:", "tok", "PATHSECRET", "SECRET1", "SECRET2", "sk-secret", "#token", "abc"):
            assert secret not in http_uri, (secret, http_uri)
        # Only the non-secret, identifiable authority is preserved.
        assert http_uri == "https://host.example.com"
        # The stdio server carries NO uri (its command basename could be a secret).
        assert "uri" not in stdio_cap, stdio_cap

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="MCP toolsets verified on pydantic-ai >=1.63.0")
    def test_manifest_mcp_uri_ipv6_and_scheme_less(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """Redactor edge cases: an IPv6 literal stays re-bracketed (valid authority) and a scheme-less ``host:port`` is
        parsed without mis-routing (its secret query still dropped). Fails if the brackets are dropped or the
        authority mis-parsed.
        """
        mcp = pytest.importorskip("pydantic_ai.mcp")
        import urllib.parse

        ipv6_server = mcp.MCPServerStreamableHTTP(url="https://[2001:db8::1]:8443/mcp?api_key=x", id="ipv6-mcp")
        schemeless_server = mcp.MCPServerStreamableHTTP(url="host.example.com:9000/mcp?pwd=SECRET", id="bare-mcp")
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", toolsets=[ipv6_server, schemeless_server])
        servers = integration._get_mcp_servers(agent)
        ipv6_uri = servers[0]["uri"]
        bare_uri = servers[1]["uri"]
        assert ipv6_uri == "https://[2001:db8::1]:8443"
        # The re-bracketed authority must round-trip back to the original host + port.
        reparsed = urllib.parse.urlsplit(ipv6_uri)
        assert reparsed.hostname == "2001:db8::1"
        assert reparsed.port == 8443
        # Scheme-less host:port is recovered (not mis-routed) and the secret query is still dropped.
        assert bare_uri == "https://host.example.com:9000"
        assert "SECRET" not in bare_uri

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="MCP toolsets verified on pydantic-ai >=1.63.0")
    def test_manifest_mcp_capability_name_is_not_credential_bearing(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """Regression: an MCP server with no ``id`` must not leak config via ``name``. ``MCPServer.label`` returns
        ``repr(self)`` (embedding url/command secrets); ``_toolset_name`` reads ``id`` then class name only. Fails if
        it reintroduces the ``label`` read.
        """
        mcp = pytest.importorskip("pydantic_ai.mcp")

        # Both servers are constructed WITHOUT an ``id``: the common case that triggers the leaky
        # ``label`` -> ``repr(self)`` default. The repr would otherwise expose every secret below.
        http_server = mcp.MCPServerStreamableHTTP(url="https://admin:pw123@h.example.com/mcp?api_key=SECRETTOK")
        stdio_server = mcp.MCPServerStdio(command="python", args=["--token=SECRETTOK"])
        # Sanity: the SDK's ``label`` really does leak here; otherwise this test proves nothing.
        assert "SECRETTOK" in http_server.label
        assert "SECRETTOK" in stdio_server.label
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", toolsets=[http_server, stdio_server])

        servers = integration._get_mcp_servers(agent)
        assert len(servers) == 2
        for cap in servers:
            for secret in ("pw123", "SECRETTOK", "admin:"):
                assert secret not in cap["name"], (secret, cap)
        # The ``name`` falls back to the class name (the only safe, constant handle).
        names = {cap["name"] for cap in servers}
        assert names == {"MCPServerStreamableHTTP", "MCPServerStdio"}, servers
        # The HTTP server still carries the redacted host; the stdio server carries no uri.
        http_cap = next(c for c in servers if c["name"] == "MCPServerStreamableHTTP")
        stdio_cap = next(c for c in servers if c["name"] == "MCPServerStdio")
        assert http_cap["uri"] == "https://h.example.com"
        assert "uri" not in stdio_cap, stdio_cap

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="custom toolsets verified on pydantic-ai >=1.63.0")
    def test_manifest_custom_toolset_name_is_not_credential_bearing(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """A custom toolset with a config-bearing ``repr`` surfaces only its class name (via ``_toolset_name``). Fails
        if ``_toolset_name`` reintroduces a ``label``/repr read.
        """
        from pydantic_ai.toolsets import AbstractToolset

        class LeakyToolset(AbstractToolset):
            @property
            def id(self):
                return None

            async def get_tools(self, ctx):
                return {}

            async def call_tool(self, name, tool_args, ctx, tool):
                raise NotImplementedError

            def __repr__(self):
                return "LeakyToolset(api_key='AKIA_CUSTOMSECRET', token='hunter2')"

        leaky = LeakyToolset()
        assert "AKIA_CUSTOMSECRET" in repr(leaky)  # the config the repr would leak
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", toolsets=[leaky])

        custom = integration._get_custom_toolsets(agent)
        assert custom == [{"name": "LeakyToolset"}], custom
        for secret in ("AKIA_CUSTOMSECRET", "hunter2"):
            assert secret not in custom[0]["name"], (secret, custom)

    @pytest.mark.skipif(
        PYDANTIC_AI_VERSION < (1, 63, 0), reason="sub-agent introspection verified on pydantic-ai >=1.63.0"
    )
    def test_manifest_delegating_tool_captured_as_plain_tool(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """A tool that delegates to another Agent is captured as a plain ``tool``, not inferred as ``sub_agent``
        (pydantic has no sub-agent construct; delegation is visible in the trace). No capability carries
        ``agent_name``. Fails-on-revert if delegation inference returns.
        """
        sub_worker = pydantic_ai.Agent(model="gpt-4o", name="sub_worker")

        def delegate_to_sub_agent(query: str) -> str:
            """Delegate the query to the sub agent."""
            return sub_worker.run_sync(query).output

        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", tools=[calculate_square_tool, delegate_to_sub_agent]
        )
        assert integration._get_agent_tools(agent) == [
            {
                "name": "calculate_square_tool",
                "description": "Calculates the square of a number",
                "parameters": {"x": {"type": "integer", "required": True}},
            },
            {
                "name": "delegate_to_sub_agent",
                "description": "Delegate the query to the sub agent.",
                "parameters": {"query": {"type": "string", "required": True}},
            },
        ]
        capabilities = {c["name"]: c for c in integration._build_capabilities(agent)}
        assert capabilities["delegate_to_sub_agent"] == {
            "name": "delegate_to_sub_agent",
            "type": "tool",
            "content": {"schema": {"query": {"type": "string", "required": True}}},
            "description": "Delegate the query to the sub agent.",
        }
        assert all(c["type"] != "sub_agent" for c in capabilities.values())
        assert all("agent_name" not in c["content"] for c in capabilities.values())

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="custom toolsets verified on pydantic-ai >=1.63.0")
    def test_manifest_custom_toolset_tools_not_over_captured_as_function_caps(
        self, pydantic_ai, pydantic_ai_llmobs, integration
    ):
        """Regression: a custom (non-Function) toolset surfaces only as a ``custom`` capability. ``_iter_agent_tools``
        reads ``.tools`` only from ``FunctionToolset``s, so a non-dict ``.tools`` neither double-counts as function
        caps nor crashes the build on ``.items()`` (which would blank the span).
        """
        from pydantic_ai.toolsets import AbstractToolset
        from pydantic_ai.toolsets import FunctionToolset

        class CustomToolset(AbstractToolset):
            @property
            def id(self):
                return "custom-ts"

            async def get_tools(self, ctx):
                return {}

            async def call_tool(self, name, tool_args, ctx, tool):
                raise NotImplementedError

        def real_tool(x: int) -> int:
            """A real function tool."""
            return x

        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", toolsets=[FunctionToolset(tools=[real_tool]), CustomToolset()]
        )
        caps = integration._build_capabilities(agent)  # must not raise
        by_type: dict = {}
        for cap in caps:
            by_type.setdefault(cap["type"], []).append(cap["name"])
        assert "real_tool" in by_type.get("tool", []), by_type
        assert by_type.get("custom") == ["custom-ts"], by_type
        # The custom toolset contributes NO function-tool capability.
        assert "custom-ts" not in by_type.get("tool", []), by_type

    @pytest.mark.skipif(
        PYDANTIC_AI_VERSION < (1, 63, 0), reason="output-function handoffs verified on pydantic-ai >=1.63.0"
    )
    def test_manifest_handoff_from_output_function(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """An output-function callable is captured as a ``handoff`` ``{tool_name, handoff_description}`` (its docstring
        is the description). No ``agent_name``: the target is not inferred.
        """
        sub_worker = pydantic_ai.Agent(model="gpt-4o", name="sub_worker")

        def route_to_sub_agent(ctx, text: str) -> str:
            """Route the request to the sub agent."""
            return sub_worker.run_sync(text).output

        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=route_to_sub_agent)
        assert integration._get_agent_output_type(agent) == {}
        assert integration._get_agent_handoffs(agent) == [
            {
                "tool_name": "route_to_sub_agent",
                "handoff_description": "Route the request to the sub agent.",
            }
        ]

    def test_manifest_build_failure_does_not_blank_agent_span(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """Regression: a manifest-build exception degrades to no-manifest, not a blanked span. ``_tag_agent_manifest``
        runs AFTER the name/input/output annotation, so those survive the raise. Fails-on-revert if the call moves
        back above the annotation.
        """
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")

        annotate_calls = []
        with mock.patch(
            "ddtrace.llmobs._integrations.pydantic_ai._annotate_llmobs_span_data",
            side_effect=lambda *a, **k: annotate_calls.append(k),
        ):
            with mock.patch.object(type(integration), "_build_agent_manifest", side_effect=RuntimeError("boom")):
                with pytest.raises(RuntimeError):
                    integration._llmobs_set_tags_agent(mock.MagicMock(), ["Hello, world!"], {"instance": agent}, None)

        annotated_keys = {key for call in annotate_calls for key in call}
        # name/input/output were annotated BEFORE the manifest raised.
        assert {"name", "input_value", "output_value"} <= annotated_keys, annotate_calls
        input_value = next((c["input_value"] for c in annotate_calls if "input_value" in c), None)
        assert input_value == "Hello, world!"
        # the manifest annotation never happened (build raised before its annotate call).
        assert not any("agent_manifest" in call for call in annotate_calls), annotate_calls

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output markers verified on pydantic-ai >=1.63.0")
    def test_manifest_tool_output_marker_keeps_schema(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """A ``ToolOutput`` wrapping a Pydantic model keeps its schema and emits no handoff."""
        from pydantic import BaseModel
        from pydantic_ai.output import ToolOutput

        class Weather(BaseModel):
            city: str

        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=ToolOutput(Weather))
        output_type = integration._get_agent_output_type(agent)
        assert output_type["name"] == "Weather"
        assert output_type["schema"]["properties"].keys() == {"city"}
        assert integration._get_agent_handoffs(agent) == []

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output markers verified on pydantic-ai >=1.63.0")
    def test_manifest_output_marker_functions_route_to_handoffs(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """``NativeOutput``/``PromptedOutput``/``TextOutput`` wrap a callable under ``.outputs``/``.output_function``
        (not ``ToolOutput``'s ``.output``): the callable routes to ``handoffs`` and ``output_type`` is not a repr-of-
        marker string. Fails if the unwrap only reads ``.output``.
        """
        from pydantic_ai.output import NativeOutput
        from pydantic_ai.output import PromptedOutput
        from pydantic_ai.output import TextOutput

        sub_worker = pydantic_ai.Agent(model="gpt-4o", name="sub_worker")

        def route(text: str) -> str:
            """Route to sub agent."""
            return sub_worker.run_sync(text).output

        # NativeOutput / PromptedOutput accept ctx-or-text callables; TextOutput requires a single
        # ``str`` arg, so ``route`` (one ``str`` param) satisfies all three.
        for marker_cls in (NativeOutput, PromptedOutput, TextOutput):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=marker_cls(route))
            output_type = integration._get_agent_output_type(agent)
            handoffs = integration._get_agent_handoffs(agent)
            # The wrapped callable is a handoff, not an output type.
            assert output_type == {}, (marker_cls.__name__, output_type)
            assert [h["tool_name"] for h in handoffs] == ["route"], (marker_cls.__name__, handoffs)
            assert "agent_name" not in handoffs[0], (marker_cls.__name__, handoffs)
            # No memory-address string leaks onto the span via output_type.
            assert "0x" not in safe_json(output_type), (marker_cls.__name__, output_type)

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output markers verified on pydantic-ai >=1.63.0")
    def test_manifest_output_marker_model_keeps_schema(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """``NativeOutput``/``PromptedOutput`` wrapping a model keep the schema (no handoff); confirms the unwrap reads
        ``.outputs`` for the model case too.
        """
        from pydantic import BaseModel
        from pydantic_ai.output import NativeOutput
        from pydantic_ai.output import PromptedOutput

        class Weather(BaseModel):
            city: str

        for marker_cls in (NativeOutput, PromptedOutput):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=marker_cls(Weather))
            output_type = integration._get_agent_output_type(agent)
            assert output_type["name"] == "Weather", (marker_cls.__name__, output_type)
            assert output_type["schema"]["properties"].keys() == {"city"}, marker_cls.__name__
            assert integration._get_agent_handoffs(agent) == [], marker_cls.__name__

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output_type verified on pydantic-ai >=1.63.0")
    def test_manifest_dataclass_output_type_with_marker_field_names(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """Regression: a plain dataclass/NamedTuple/Enum whose members collide with marker attr names
        (``output``/``outputs``/``output_function``) must not be duck-typed as a marker. ``_marker_target`` gates on
        ``isinstance`` first; the class is named and no handoff is fabricated. Fails if the attr is read before the
        gate.
        """
        from dataclasses import dataclass
        from enum import Enum
        from typing import NamedTuple

        def some_callable():
            return None

        @dataclass
        class DataclassWithDefault:
            output: str = "SENSITIVE_DEFAULT"

        @dataclass
        class DataclassMisrouted:
            # ``output`` holds a callable and ``description`` is set: the duck-typed path produced an
            # empty output_type plus a fabricated handoff leaking the field name + the description.
            output: object = some_callable
            description: str = "INTERNAL_DESC_LEAK"

        class NamedTupleOut(NamedTuple):
            output: str = "x"

        class EnumOut(Enum):
            output = "RED"

        cases = [DataclassWithDefault, DataclassMisrouted, NamedTupleOut, EnumOut]
        for output_type_cls in cases:
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=output_type_cls)
            output_type = integration._get_agent_output_type(agent)
            # The class name is kept; no field default / descriptor / ``Enum.member`` leaks as ``name``.
            assert output_type == {"name": output_type_cls.__name__}, (output_type_cls.__name__, output_type)
            # No handoff is fabricated from a member that merely shares a marker attr name.
            assert integration._get_agent_handoffs(agent) == [], output_type_cls.__name__

    @pytest.mark.skipif(
        PYDANTIC_AI_VERSION < (1, 63, 0), reason="dynamic instructions verified on pydantic-ai >=1.63.0"
    )
    async def test_agent_run_with_dynamic_instructions(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """A dynamic (callable) instructions function is captured in ``extra_instructions`` as a ``{type:
        dynamic_instructions, content:{name, source_hash?, reevaluated}}`` entry; the frozen ``instructions`` key
        stays str|None (None here, no static text).
        """
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")

            @agent.instructions
            def dynamic_instructions(ctx) -> str:
                """Resolved per run."""
                return "computed instructions"

            result = await agent.run("Hello, world!")
        span_data = pop_single_agent_span(test_spans)
        assert_llmobs_span_data(
            span_data,
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            tags=PYDANTIC_AI_TAGS,
        )
        manifest = span_data["meta"]["metadata"]["_dd"]["agent_manifest"]
        descriptor = extract_extra_instruction(manifest, "dynamic_instructions")
        assert descriptor["name"] == "dynamic_instructions"
        assert descriptor["reevaluated"] is True
        assert_source_hash(descriptor)
        assert manifest["instructions"] is None

    def test_manifest_restores_system_prompts_and_omits_dependencies(
        self, pydantic_ai, pydantic_ai_llmobs, integration
    ):
        """A static ``system_prompt`` is captured in ``system_prompts``; no ``dependencies`` key is emitted (the dep
        type surfaces under ``agent_settings.deps_type``).
        """

        class SupportDeps:
            pass

        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", system_prompt="you are a bot", deps_type=SupportDeps
        )
        manifest = integration._build_agent_manifest(agent)
        # system_prompts is restored as the frozen LIST (static-only -> no extra_instructions key).
        assert list(manifest["system_prompts"]) == ["you are a bot"]
        assert "extra_instructions" not in manifest
        # The removed dependencies block stays gone; deps_type surfaces under the flat agent_settings.
        assert "dependencies" not in manifest
        assert manifest["agent_settings"].get("deps_type") == "SupportDeps"

    def test_manifest_captures_dynamic_instructions_all_versions(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """Version-drift load-bearer: a dynamic ``@agent.instructions`` function is captured on every pin. It lives in
        ``_instructions`` (>=1.63.0) or ``_instructions_functions`` (<1.63.0); the pre-enrichment code read only the
        former and missed it on 0.8.1/1.0.0. Fails if the collector stops reading ``_instructions_functions``.
        """
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")

        @agent.instructions
        def dynamic_instructions(ctx) -> str:
            """Resolved per run."""
            return "computed instructions"

        manifest = integration._build_agent_manifest(agent)
        descriptor = extract_extra_instruction(manifest, "dynamic_instructions")
        assert descriptor["name"] == "dynamic_instructions"
        assert_source_hash(descriptor)
        assert manifest["instructions"] is None

    def test_manifest_captures_static_and_dynamic_system_prompts(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """A static ``system_prompt`` and a dynamic ``@agent.system_prompt`` are both captured: the static string in
        ``system_prompts``, the dynamic fn as a ``dynamic_system_prompt`` entry in ``extra_instructions``. Single-
        homed (source not mirrored into the static string). Builder-driven.
        """
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", system_prompt="You are a bot.")

        @agent.system_prompt
        def dynamic_system_prompt(ctx) -> str:
            """Adds the user name."""
            return "computed system prompt"

        manifest = integration._build_agent_manifest(agent)
        # The frozen ``system_prompts`` LIST holds the static string ONLY; the dynamic function is in the
        # additive ``extra_instructions`` list, typed ``dynamic_system_prompt``.
        assert list(manifest["system_prompts"]) == ["You are a bot."]
        descriptor = extract_extra_instruction(manifest, "dynamic_system_prompt")
        assert descriptor["name"] == "dynamic_system_prompt"
        assert_source_hash(descriptor)
        # A plain ``@agent.system_prompt`` runs once (not re-evaluated each step).
        assert descriptor["reevaluated"] is False
        # The dynamic function is single-homed in extra_instructions, not mirrored into system_prompts.
        assert "def dynamic_system_prompt" not in " ".join(manifest["system_prompts"])

    def test_manifest_prompt_source_is_hashed_not_emitted(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """The function source is hashed, never emitted: a 100k-char source yields only a 64-char ``source_hash`` and no
        ``source`` key. Fails-on-revert if the builder stores the raw source again.
        """

        def dynamic_instructions(ctx) -> str:
            """Resolved per run."""
            return "x"

        oversized_source = "x" * 100_000
        with mock.patch("ddtrace.llmobs._integrations.pydantic_ai.inspect.getsource", return_value=oversized_source):
            described = integration._describe_functions([dynamic_instructions])
        assert len(described) == 1
        assert described[0]["name"] == "dynamic_instructions"
        assert "source" not in described[0]
        assert described[0]["source_hash"] == hashlib.sha256(oversized_source.encode("utf-8")).hexdigest()

    def test_manifest_prompt_source_unavailable_omits_hash(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """A function with no retrievable source (``getsource`` raises ``OSError``/``TypeError``: lambda, REPL,
        C-implemented) degrades to a name-only descriptor: ``name`` present, no ``source_hash``, no raise. Fails if
        the ``getsource`` guard is removed.
        """

        def dynamic_instructions(ctx) -> str:
            """Resolved per run."""
            return "x"

        with mock.patch("ddtrace.llmobs._integrations.pydantic_ai.inspect.getsource", side_effect=OSError("no source")):
            described = integration._describe_functions([dynamic_instructions])
        assert len(described) == 1
        descriptor = described[0]
        assert descriptor["name"] == "dynamic_instructions"
        assert "source_hash" not in descriptor
        assert "source" not in descriptor

    def test_manifest_unnamed_callable_recovers_identity_not_faked(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """A callable with no ``__name__`` (``functools.partial``, callable instance) is named by recoverable identity
        (``func.__name__`` or the class), never a faked constant that collapses distinct callables. Fails-on-revert
        if a constant placeholder returns.
        """
        import functools

        def base_trimmer(messages):
            return messages

        def base_summarizer(messages):
            return messages

        class Redactor:
            def __call__(self, messages):
                return messages

        described = integration._describe_functions(
            [functools.partial(base_trimmer), functools.partial(base_summarizer), Redactor()]
        )
        names = [d["name"] for d in described]
        assert names == ["base_trimmer", "base_summarizer", "Redactor"], described
        # The point: distinct unnamed callables stay distinct (no collapse to a shared "function").
        assert len(set(names)) == 3
        assert "function" not in names

    @pytest.mark.skipif(
        PYDANTIC_AI_VERSION < (1, 63, 0), reason="mixed _instructions list shape verified on pydantic-ai >=1.63.0"
    )
    def test_manifest_mixed_instructions_list_splits_static_and_dynamic(
        self, pydantic_ai, pydantic_ai_llmobs, integration
    ):
        """Regression (>=1.63.0): ``_instructions`` is a list mixing a static string and a raw callable; the collector
        routes the string to ``instructions`` and describes the callable once in ``extra_instructions``. Fails if the
        list-split breaks.
        """
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", instructions="You are a helpful assistant.")

        @agent.instructions
        def dynamic_instructions(ctx) -> str:
            """Resolved per run."""
            return "computed instructions"

        # Sanity: this version really does store the callable inside the _instructions list.
        assert isinstance(agent._instructions, list)
        assert any(callable(entry) for entry in agent._instructions)

        manifest = integration._build_agent_manifest(agent)
        # The frozen ``instructions`` is the static string ONLY (the dynamic source is not appended).
        assert manifest["instructions"] == "You are a helpful assistant."
        assert "def dynamic_instructions" not in manifest["instructions"]
        # De-duped: the single callable is described exactly once in ``extra_instructions``.
        descriptor = extract_extra_instruction(manifest, "dynamic_instructions")
        assert descriptor["name"] == "dynamic_instructions"

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="multi-element static instructions list is >=1.63.0")
    def test_manifest_multi_static_instructions_joined_with_newline(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """Multiple static instruction strings join with a newline (pydantic-ai's own separator), not a space, so ``["a
        b"]`` and ``["a", "b"]`` stay distinguishable. Fails-on-revert if the join uses a space.
        """
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", instructions=["You are helpful.", "Be concise."])
        manifest = integration._build_agent_manifest(agent)
        assert manifest["instructions"] == "You are helpful.\nBe concise."

    def test_manifest_agent_settings_retries_version_tolerant(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """Regression: ``retries`` survives the >1.63.0 rename ``_max_result_retries`` -> ``_max_output_retries`` (reads
        the successor when the old name is absent). ``tool_retries`` is the distinct per-tool budget, set divergently
        here to prove the two are never crossed. Fails against a ``_max_result_retries``-only read.
        """
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", retries=2)
        # Sanity: the unmodified agent already reports the ctor retries on the current pin.
        assert integration._get_agent_settings(agent)["retries"] == 2

        # Simulate the >1.63.0 attribute shape regardless of the installed version: drop the old
        # name and pin the successor, with a DIFFERENT tool budget to catch an output/tool mix-up.
        if hasattr(agent, "_max_result_retries"):
            delattr(agent, "_max_result_retries")
        agent._max_output_retries = 2
        agent._max_tool_retries = 7

        assert not hasattr(agent, "_max_result_retries")
        settings = integration._get_agent_settings(agent)
        assert settings["retries"] == 2, settings
        manifest = integration._build_agent_manifest(agent)
        settings = manifest["agent_settings"]
        assert settings["retries"] == 2, settings
        # The per-tool budget is emitted separately as ``tool_retries`` and is NOT crossed with output.
        assert settings["tool_retries"] == 7, settings

    def test_manifest_top_level_shape(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """The manifest is flat: identity + model + ``instructions``/``tools`` + ``capabilities`` + ``output_type`` +
        flat ``agent_settings``, with no grouped ``behaviors`` key or ``definition`` wrapper.
        """
        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", instructions="You are helpful.", tools=[calculate_square_tool]
        )
        manifest = integration._build_agent_manifest(agent)

        assert manifest["framework"] == "PydanticAI"
        assert manifest["name"] == "test_agent"
        assert manifest["model"] == "gpt-4o"
        assert manifest["model_provider"] == "openai"
        # Frozen: ``instructions`` is a plain string; ``tools`` is the flat back-compat list; its entries
        # carry NO ``type`` key (that lives on the ``capabilities`` envelope).
        assert manifest["instructions"] == "You are helpful."
        assert [t["name"] for t in manifest["tools"]] == ["calculate_square_tool"]
        assert "type" not in manifest["tools"][0]
        # The function tool is ADDITIONALLY surfaced in the unified ``capabilities`` superset, typed.
        assert [(c["name"], c["type"]) for c in manifest["capabilities"]] == [("calculate_square_tool", "tool")]
        assert manifest["output_type"] == {"name": "str"}
        # ``agent_settings`` is a flat top-level key (no ``behaviors`` grouping); retry budgets are 1/1 on
        # every supported pin (``end_strategy`` / ``deps_type`` defaults vary by version, so only assert shape).
        assert "behaviors" not in manifest
        settings = manifest["agent_settings"]
        assert settings["retries"] == 1
        assert settings["tool_retries"] == 1
        assert "end_strategy" in settings
        # No legacy ``definition`` wrapper.
        assert "definition" not in manifest

    def test_manifest_additive_over_shipped_contract(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """Additive over the shipped manifest: ``instructions`` (str|None) / ``system_prompts`` (list) / ``tools``
        (always present) keep their exact name and type, and ``capabilities`` is a sibling of ``tools`` (never a
        replacement). Checks a minimal and a rich agent.
        """
        from pydantic import BaseModel

        class Out(BaseModel):
            city: str

        rich = pydantic_ai.Agent(
            model="gpt-4o",
            name="rich",
            instructions="You are helpful.",
            system_prompt="Be concise.",
            tools=[calculate_square_tool],
            output_type=Out,
        )
        minimal = pydantic_ai.Agent(model="gpt-4o", name="minimal")

        for agent in (minimal, rich):
            m = integration._build_agent_manifest(agent)
            # ``tools`` is always present and a flat list (never folded into ``capabilities``).
            assert isinstance(m["tools"], list), m
            # ``instructions`` keeps the shipped type: str or None (never a dict).
            assert m["instructions"] is None or isinstance(m["instructions"], str), m
            # ``system_prompts`` keeps the shipped sequence type (list/tuple, never a dict).
            assert isinstance(m["system_prompts"], (list, tuple)), m
            # The ``behaviors`` grouping was never adopted.
            assert "behaviors" not in m, m

        # The rich agent's function tool lives in BOTH the flat ``tools`` list and the additive
        # ``capabilities`` superset; the minimal agent (no tools) has no ``capabilities`` key.
        rich_m = integration._build_agent_manifest(rich)
        assert [t["name"] for t in rich_m["tools"]] == ["calculate_square_tool"]
        assert "calculate_square_tool" in {c["name"] for c in rich_m["capabilities"]}
        assert "capabilities" not in integration._build_agent_manifest(minimal)

    def test_manifest_captures_guardrails(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """``@agent.output_validator`` functions are captured as ``guardrails`` ``{name, source_hash}``. Validators
        chain (each sees the prior's output), so order is semantic and preserved as registered, not sorted.
        """
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")

        @agent.output_validator
        def strip_pii(ctx, output):
            """Strip PII from the response."""
            return output

        @agent.output_validator
        def reject_ungrounded(ctx, output):
            """Reject ungrounded output."""
            return output

        manifest = integration._build_agent_manifest(agent)
        guardrails = {g["name"]: g for g in manifest["guardrails"]}
        # Registration order is preserved (strip_pii registered first), NOT sorted alphabetically.
        assert [g["name"] for g in manifest["guardrails"]] == ["strip_pii", "reject_ungrounded"]
        assert_source_hash(guardrails["reject_ungrounded"])
        assert_source_hash(guardrails["strip_pii"])

    def test_manifest_guardrails_order_is_preserved(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """Guardrail order is semantic (validators chain), so reversed registration yields a different manifest. Fails-
        on-revert if the guardrails list is sorted.
        """

        def alpha(ctx, output):
            return output

        def bravo(ctx, output):
            return output

        def names_for(order):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            for validator in order:
                agent.output_validator(validator)
            return [g["name"] for g in integration._build_agent_manifest(agent)["guardrails"]]

        assert names_for([alpha, bravo]) == ["alpha", "bravo"]
        assert names_for([bravo, alpha]) == ["bravo", "alpha"]

    def test_manifest_captures_tool_transforms(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """``prepare_tools``/``prepare_output_tools`` are captured as ``tool_transforms`` with a ``scope`` (they rewrite
        the tool set per run, part of the definition).
        """

        async def prepare_tools(ctx, tool_defs):
            """Filter tools per run."""
            return tool_defs

        async def prepare_output_tools(ctx, tool_defs):
            """Filter output tools per run."""
            return tool_defs

        agent = pydantic_ai.Agent(
            model="gpt-4o",
            name="test_agent",
            prepare_tools=prepare_tools,
            prepare_output_tools=prepare_output_tools,
        )
        manifest = integration._build_agent_manifest(agent)
        scopes = {t["name"]: t["scope"] for t in manifest["tool_transforms"]}
        assert scopes == {"prepare_tools": "tools", "prepare_output_tools": "output_tools"}

    def test_manifest_history_processors_captured(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """``history_processors`` (the memory policy) are captured under the flat ``memory_policies`` key and described
        like any prompt function. Builder-driven: bare callables on every pin, no ``.function`` unwrap needed.
        """

        def keep_last_message(messages):
            """Trim history to the most recent message."""
            return messages[-1:]

        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", history_processors=[keep_last_message])
        processors = integration._build_agent_manifest(agent)["memory_policies"]
        assert len(processors) == 1
        assert processors[0]["name"] == "keep_last_message"
        # One real-source (unmocked) path digest-checks the actual function body, binding the hash to the
        # real getsource read; the other source_hash sites assert shape only, to avoid bloat.
        assert (
            processors[0]["source_hash"]
            == hashlib.sha256(inspect.getsource(keep_last_message).encode("utf-8")).hexdigest()
        )
        assert "source" not in processors[0]

    def test_manifest_history_processors_omitted_when_absent(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """An agent with no history processors emits no ``memory_policies`` key."""
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
        assert "memory_policies" not in integration._build_agent_manifest(agent)

    def test_manifest_history_processors_order_is_preserved(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """History processors run as a pipeline, so order is semantic: reversed registration yields a different
        manifest. Must not be sorted.
        """

        def alpha(messages):
            return messages

        def beta(messages):
            return messages

        m_ab = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", history_processors=[alpha, beta])
        )
        m_ba = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", history_processors=[beta, alpha])
        )
        assert [p["name"] for p in m_ab["memory_policies"]] == ["alpha", "beta"]
        assert [p["name"] for p in m_ba["memory_policies"]] == ["beta", "alpha"]

    def test_manifest_tools_preserve_registration_order(self, pydantic_ai, pydantic_ai_llmobs, integration):
        """The ``tools`` list preserves registration order (not sorted): reversed registration yields a different
        ordering.
        """
        manifest_ab = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", tools=[calculate_square_tool, foo_tool])
        )
        manifest_ba = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", tools=[foo_tool, calculate_square_tool])
        )
        assert [t["name"] for t in manifest_ab["tools"]] == ["calculate_square_tool", "foo_tool"]
        assert [t["name"] for t in manifest_ba["tools"]] == ["foo_tool", "calculate_square_tool"]

    async def test_agent_run_stream(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream(debounce_by=None):
                    output = chunk
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
        )

    @pytest.mark.parametrize("delta", [False, True])
    async def test_agent_run_stream_text(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, delta):
        """``delta`` selects whether each chunk is the full output so far or just the increment from the
        previous chunk.
        """
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream_text(debounce_by=None, delta=delta):
                    output = output + chunk if delta else chunk
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
        )

    @pytest.mark.parametrize("stream_method", ["stream_structured", "stream_responses"])
    async def test_agent_run_stream_method(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, stream_method
    ):
        if stream_method == "stream_responses" and PYDANTIC_AI_VERSION < (0, 8, 1):
            pytest.skip("pydantic-ai < 0.8.1 does not support stream_responses")

        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                stream_func = getattr(result, stream_method)
                async for chunk in stream_func():
                    output = chunk[0].parts[0].content
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (0, 8, 1), reason="pydantic-ai < 0.8.1 does not support stream_responses")
    async def test_agent_run_stream_responses_early_exit(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Test that the span is still finished when the stream is exited early"""
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk, last in result.stream_responses():
                    assert not last  # assert this is not the last chunk
                    output = chunk.parts[0].content
                    break
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
        )

    async def test_agent_run_stream_get_output(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                output = await result.get_output()
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
        )

    async def test_agent_run_stream_with_tool(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_with_tools.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o", name="test_agent", tools=[calculate_square_tool], instructions=instructions
            )
            async with agent.run_stream("What is the square of 2?") as result:
                async for chunk in result.stream():
                    output = chunk
        agent_span_data, tool_span_data, agent_span_id = pop_agent_and_tool_spans(test_spans)
        assert_llmobs_span_data(
            agent_span_data,
            span_kind="agent",
            name="test_agent",
            input_value="What is the square of 2?",
            output_value=output,
            metadata=expected_agent_metadata(instructions=instructions, tools=expected_calculate_square_tool()),
            tags=PYDANTIC_AI_TAGS,
        )
        assert_calculate_square_tool_span(tool_span_data, agent_span_id)

    @pytest.mark.parametrize("stream_method", ["stream_structured", "stream_responses"])
    async def test_agent_run_stream_method_with_tool(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, stream_method
    ):
        if stream_method == "stream_responses" and PYDANTIC_AI_VERSION < (0, 8, 1):
            pytest.skip("pydantic-ai < 0.8.1 does not support stream_responses")

        class Output(TypedDict):
            original_number: int
            square: int

        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_structured_with_tool.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o",
                name="test_agent",
                tools=[calculate_square_tool],
                instructions=instructions,
                output_type=Output,
            )
            async with agent.run_stream("What is the square of 2?") as result:
                stream_func = getattr(result, stream_method)
                async for chunk in stream_func(debounce_by=None):
                    output = chunk
        agent_span_data, tool_span_data, agent_span_id = pop_agent_and_tool_spans(test_spans)
        assert_llmobs_span_data(
            agent_span_data,
            span_kind="agent",
            name="test_agent",
            input_value="What is the square of 2?",
            output_value=safe_json(output[0].parts[0].args, ensure_ascii=False),
            metadata=expected_agent_metadata(
                instructions=instructions, tools=expected_calculate_square_tool(), output_type={"name": "Output"}
            ),
            tags=PYDANTIC_AI_TAGS,
        )
        assert_calculate_square_tool_span(tool_span_data, agent_span_id)

    async def test_agent_run_stream_error(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            with pytest.raises(Exception, match="test error"):
                async with agent.run_stream("Hello, world!") as result:
                    stream = result.stream(debounce_by=None)
                    async for chunk in stream:
                        output = chunk
                        raise Exception("test error")

        assert_single_agent_span(
            test_spans,
            name=None,
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            error={"type": "builtins.Exception", "message": "test error", "stack": mock.ANY},
        )

    async def test_agent_iter(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.iter("Hello, world!") as agent_run:
                async for _ in agent_run:
                    pass
                output = agent_run.result.output
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
        )

    async def test_agent_iter_error(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            with pytest.raises(Exception, match="test error"):
                async with agent.iter("Hello, world!") as agent_run:
                    async for _ in agent_run:
                        raise Exception("test error")

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        agent_span_data = _get_llmobs_data_metastruct(spans[0])
        assert agent_span_data["meta"]["error"]["message"] == "test error"
        assert spans[0].error == 1

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (0, 4, 4), reason="pydantic-ai < 0.4.4 does not support toolsets")
    async def test_agent_run_with_toolset(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """Test that the agent manifest includes tools from both the function toolset and the user-defined toolsets"""
        from pydantic_ai.toolsets import FunctionToolset

        with request_vcr.use_cassette("agent_run_stream_with_toolset.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o",
                name="test_agent",
                toolsets=[FunctionToolset(tools=[calculate_square_tool])],
                tools=[foo_tool],
            )
            result = await agent.run("Hello, world!")
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(tools=expected_calculate_square_tool() + expected_foo_tool()),
        )

    @pytest.mark.parametrize("invoke", ["run", "run_stream", "iter"])
    async def test_agent_message_history_sets_input_value(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, invoke
    ):
        """INPUT_VALUE comes from ``message_history`` when no ``user_prompt`` is passed, across run / run_stream / iter
        (output extraction differs; input-from-history is shared).
        """
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        cassette = "agent_run_stream.yaml" if invoke == "run_stream" else "agent_iter.yaml"
        with request_vcr.use_cassette(cassette):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            if invoke == "run":
                result = await agent.run(message_history=message_history)
                output = result.output
            elif invoke == "run_stream":
                output = ""
                async with agent.run_stream(message_history=message_history) as result:
                    async for chunk in result.stream(debounce_by=None):
                        output = chunk
            else:  # iter
                async with agent.iter(message_history=message_history) as agent_run:
                    async for _ in agent_run:
                        pass
                    output = agent_run.result.output
        assert_single_agent_span(
            test_spans,
            input_value="Hello from history!",
            output_value=output,
            metadata=expected_agent_metadata(),
        )

    async def test_agent_run_with_user_prompt_and_message_history(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Test that user_prompt takes precedence over message_history."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = await agent.run("Hello, world!", message_history=message_history)
        assert_single_agent_span(
            test_spans,
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
        )

    async def test_agent_run_with_unserializable_model_settings(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """Regression: ``model_settings`` with non-JSON-serializable provider sentinels must not crash span submission.
        Uses ``FunctionModel`` to avoid OpenAI SDK serialization rejecting the sentinel first.
        """
        from pydantic_ai.messages import ModelResponse
        from pydantic_ai.messages import TextPart
        from pydantic_ai.models.function import FunctionModel

        def model_func(messages, info):
            return ModelResponse(parts=[TextPart(content="Hello!")])

        agent = pydantic_ai.Agent(
            model=FunctionModel(model_func),
            name="test_agent",
            model_settings={"temperature": _UnserializableSentinel(), "max_tokens": 100},
        )
        await agent.run("Hello, world!")
        span_data = pop_single_agent_span(test_spans)
        recorded_settings = span_data["meta"]["metadata"]["_dd"]["agent_manifest"]["model_settings"]
        # Coerced values must be JSON-serializable.
        json.dumps(recorded_settings)
        assert recorded_settings["max_tokens"] == 100
        assert recorded_settings["temperature"] == "Omit()"


class TestLLMObsPydanticAISpanLinks:
    async def test_agent_calls_tool(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, openai_patched, test_spans):
        instructions = "Use the provided tool to calculate the square of 2."
        with request_vcr.use_cassette("agent_run_stream_with_tools.yaml"):
            agent = pydantic_ai.Agent(
                model="gpt-4o", name="test_agent", tools=[calculate_square_tool], instructions=instructions
            )
            async with agent.run_stream("What is the square of 2?") as result:
                async for _ in result.stream(debounce_by=None):
                    pass

        trace = test_spans.pop_traces()[0]
        # APM trace order: agent → first LLM call → tool → second LLM call.
        first_llm_span = trace[1]
        tool_span = trace[2]
        second_llm_span = trace[3]
        tool_span_data = _get_llmobs_data_metastruct(tool_span)
        second_llm_span_data = _get_llmobs_data_metastruct(second_llm_span)

        # LLM-to-tool span link should be on the tool span, pointing at the first LLM span.
        assert len(tool_span_data["span_links"]) == 1
        assert tool_span_data["span_links"][0]["span_id"] == str(first_llm_span.span_id)
        assert tool_span_data["span_links"][0]["attributes"] == {"from": "output", "to": "input"}
        # tool-to-LLM span link should be on the second LLM span, pointing at the tool span.
        assert len(second_llm_span_data["span_links"]) == 1
        assert second_llm_span_data["span_links"][0]["span_id"] == str(tool_span.span_id)
        assert second_llm_span_data["span_links"][0]["attributes"] == {"from": "output", "to": "input"}


class _UnserializableSentinel:
    """Stand-in for provider sentinels such as OpenAI's ``Omit`` / ``NOT_GIVEN``."""

    def __repr__(self):
        return "Omit()"


def test_model_settings_unserializable_values_are_coerced():
    """Regression: non-JSON-serializable ``model_settings`` sentinels (OpenAI ``Omit``/``NOT_GIVEN``) are coerced to
    JSON-safe values, not stored raw (which crashed the trace encoder at span finish).
    """
    raw = {"temperature": _UnserializableSentinel(), "max_tokens": 100}
    # This is what used to be stored raw on the span and crash encoding.
    with pytest.raises(TypeError):
        json.dumps(raw)

    coerced = load_data_value(raw)
    json.dumps(coerced)  # must not raise
    assert coerced["max_tokens"] == 100
    assert coerced["temperature"] == "Omit()"


def test_model_settings_none_is_preserved():
    assert load_data_value(None) is None
