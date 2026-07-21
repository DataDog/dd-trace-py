import json

import mock
import pydantic_ai
import pytest
from typing_extensions import TypedDict

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.pydantic_ai import _METADATA_MAX_BYTES
from ddtrace.llmobs._integrations.pydantic_ai import _PROMPT_SOURCE_MAX_BYTES
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import load_data_value
from ddtrace.llmobs._utils import safe_json
from tests.contrib.pydantic_ai.utils import PYDANTIC_AI_TAGS
from tests.contrib.pydantic_ai.utils import calculate_square_tool
from tests.contrib.pydantic_ai.utils import expected_agent_metadata
from tests.contrib.pydantic_ai.utils import expected_calculate_square_tool
from tests.contrib.pydantic_ai.utils import expected_foo_tool
from tests.contrib.pydantic_ai.utils import foo_tool
from tests.llmobs._utils import assert_llmobs_span_data


PYDANTIC_AI_VERSION = parse_version(pydantic_ai.__version__)

TOOL_DESCRIPTION_METADATA = {"description": "Calculates the square of a number"}


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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(
                instructions=instructions,
                model_settings=model_settings,
                tools=expected_calculate_square_tool(),
                system_prompts=system_prompt,
            ),
            tags=PYDANTIC_AI_TAGS,
        )

    def test_agent_run_sync(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = agent.run_sync("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    async def test_agent_run_with_metadata(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """An agent's statically-configured ``metadata`` dict is captured into the agent manifest."""
        agent_metadata = {"version": "v2", "team": "billing"}
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata=agent_metadata)
            result = await agent.run("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(metadata=agent_metadata),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    async def test_agent_run_with_callable_metadata_not_captured(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Callable ``metadata`` is not statically serializable, so it is omitted from the manifest."""
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata=lambda ctx: {"version": "dyn"})
            result = await agent.run("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    def test_manifest_metadata_is_deep_copied_not_aliased(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: the manifest stores a DEEP copy of ``agent._metadata``, not the live reference.

        The same ``Agent`` is reused across runs and ``_writer._enqueue`` buffers the live event dict
        until the periodic flush, so aliasing ``_metadata`` (even a shallow ``dict(...)`` copy, which
        still shares nested containers) would let a later mutation rewrite the already-queued span
        event. Builder-driven so no model call is needed. Fails if the integration stores
        ``agent._metadata`` directly or via a shallow copy.
        """
        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", metadata={"team": "billing", "nested": {"k": "RUN1"}}
        )

        manifest = integration._build_agent_manifest(agent)
        assert manifest["metadata"] == {"team": "billing", "nested": {"k": "RUN1"}}
        assert manifest["metadata"] is not agent._metadata
        assert manifest["metadata"]["nested"] is not agent._metadata["nested"]

        # A NESTED mutation after the manifest is built must not leak into the captured copy -- a
        # shallow copy would share the inner dict and fail this assertion.
        agent._metadata["nested"]["k"] = "RUN2"
        agent._metadata["new_key"] = "leaked"
        assert manifest["metadata"]["nested"]["k"] == "RUN1"
        assert manifest["metadata"] == {"team": "billing", "nested": {"k": "RUN1"}}

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    def test_manifest_metadata_unserializable_is_truncated(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: ``metadata`` that ``safe_json`` cannot serialize is dropped, not stored.

        ``safe_json`` returns ``None`` for a dict with mixed-type keys at one level (``sort_keys``
        cannot order ``int`` vs ``str``). If such a dict reached the manifest, ``_writer.enqueue``'s
        unguarded ``len(safe_json(event))`` would raise and the whole agent span would be silently
        dropped. The manifest must instead flag ``metadata_truncated`` and build without raising.
        Fails if the integration routes a ``None`` serialization to the store branch.
        """
        integration = pydantic_ai._datadog_integration

        poison_metadata = {"x": {1: "a", "b": 2}}
        assert safe_json(poison_metadata) is None  # precondition: this dict is not JSON-serializable
        manifest = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata=poison_metadata)
        )
        assert "metadata" not in manifest
        assert manifest["metadata_truncated"] is True

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    def test_manifest_metadata_is_bounded(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: an oversized ``metadata`` dict is dropped (flagged) rather than emitted.

        ``metadata`` rides every agent span and ``_truncate_span_event`` drops ``meta.input``/
        ``meta.output`` but not the manifest, so a fat dict could evict the user's real I/O under the
        span size cap. When it serializes over the byte budget the field is dropped and
        ``metadata_truncated`` flags it; a small dict is captured unchanged. Fails if the integration
        stores ``agent._metadata`` unbounded.
        """
        integration = pydantic_ai._datadog_integration

        big_metadata = {f"key_{i}": "v" * 100 for i in range(200)}  # serializes well over the byte budget
        assert len(safe_json(big_metadata)) > _METADATA_MAX_BYTES
        big_manifest = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata=big_metadata)
        )
        assert "metadata" not in big_manifest
        assert big_manifest["metadata_truncated"] is True

        small_manifest = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata={"version": "v2"})
        )
        assert small_manifest["metadata"] == {"version": "v2"}
        assert "metadata_truncated" not in small_manifest

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    def test_manifest_empty_metadata_omitted(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: an empty / unset ``metadata`` dict yields neither key.

        Guards the existing ``isinstance(dict) and agent_metadata`` behavior so the copy/cap rework
        does not start emitting ``metadata`` or ``metadata_truncated`` for an agent with no metadata.
        """
        integration = pydantic_ai._datadog_integration

        empty_manifest = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata={})
        )
        assert "metadata" not in empty_manifest
        assert "metadata_truncated" not in empty_manifest

        default_manifest = integration._build_agent_manifest(pydantic_ai.Agent(model="gpt-4o", name="test_agent"))
        assert "metadata" not in default_manifest
        assert "metadata_truncated" not in default_manifest

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="agent attrs verified on pydantic-ai >=1.63.0")
    async def test_agent_run_with_deps_type_in_agent_settings(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """The dependency-injection deps type is captured into the flat ``manifest.settings.deps_type``."""

        class SupportDeps:
            pass

        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", deps_type=SupportDeps)
            result = await agent.run("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(
                settings={"retries": 1, "tool_retries": 1, "end_strategy": "early", "deps_type": "SupportDeps"},
            ),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="builtin tool kinds verified on pydantic-ai >=1.63.0")
    def test_agent_run_with_builtin_capability(self, pydantic_ai, pydantic_ai_llmobs):
        """A builtin tool is captured as a typed ``builtin`` entry in the unified ``capabilities`` list.

        Built directly from the constructed agent (no run): a provider-hosted builtin like
        ``WebSearchTool`` is model-gated at run time (``OpenAIChatModel`` rejects it), but the manifest
        reads only the configured ``_builtin_tools``, so a run is unnecessary and version-fragile.
        """
        from pydantic_ai.builtin_tools import WebSearchTool

        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", builtin_tools=[WebSearchTool()])
        capabilities = integration._build_capabilities(agent)
        builtins = [c["name"] for c in capabilities if c["type"] == "builtin"]
        assert builtins == ["web_search"], capabilities

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output_type/markers verified on pydantic-ai >=1.63.0")
    def test_agent_run_with_structured_output_type(self, pydantic_ai, pydantic_ai_llmobs):
        """A Pydantic ``BaseModel`` output type is captured as ``output_type`` with a JSON schema.

        Built directly from the constructed agent (no run): a structured ``output_type`` changes the
        model request shape, which drifts from the recorded cassette across SDK versions. The manifest
        reads only ``agent.output_type``, so a run is unnecessary and version-fragile.
        """
        from pydantic import BaseModel

        class Weather(BaseModel):
            city: str
            temperature: int

        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=Weather)
        output_type = integration._get_agent_output_type(agent)
        assert output_type["name"] == "Weather"
        # schema is the Pydantic-generated JSON schema for the model
        assert output_type["schema"]["properties"].keys() == {"city", "temperature"}
        assert output_type["schema"]["required"] == ["city", "temperature"]
        # callables are NOT captured here -- so a structured-only agent has no handoffs
        assert integration._get_agent_handoffs(agent) == []

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output_type verified on pydantic-ai >=1.63.0")
    def test_manifest_output_type_union_captures_all_alternatives(self, pydantic_ai, pydantic_ai_llmobs):
        """A multi-output union captures ALL alternatives, not just the first.

        ``output_type=[Fruit, Vehicle]`` previously collapsed to ``Fruit`` (indistinguishable from a single
        ``Fruit`` output). Now ``name`` joins the members and ``schema`` is the union (``anyOf``), so a
        change to any alternative -- or adding/removing one -- is reflected. Output *functions* still route
        to ``handoffs``, not here. Fails-on-revert: collapse to the first model and the union == single.
        """
        from pydantic import BaseModel

        class Fruit(BaseModel):
            kind: str

        class Vehicle(BaseModel):
            wheels: int

        integration = pydantic_ai._datadog_integration
        union = integration._get_agent_output_type(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=[Fruit, Vehicle])
        )
        assert union["name"] == "Fruit | Vehicle"
        # The union schema encodes BOTH alternatives, so it differs from a single-output Fruit.
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
    def test_manifest_mcp_capability(self, pydantic_ai, pydantic_ai_llmobs):
        """An MCP toolset is captured as a typed ``mcp`` capability entry.

        MCP servers connect lazily, so we assert the builder output rather than running the agent. An
        HTTP server (``.url``) carries a scrubbed ``scheme://host`` ``uri``; a stdio server
        (``.command``) carries NO ``uri`` (its executable basename can be a secret) -- the ``name``
        identifies it. The ``mcp`` extra is optional, so skip when it is not installed.
        """
        mcp = pytest.importorskip("pydantic_ai.mcp")

        integration = pydantic_ai._datadog_integration
        stdio_server = mcp.MCPServerStdio(command="echo", args=["hi"], id="stdio-mcp")
        http_server = mcp.MCPServerStreamableHTTP(url="https://example.com/mcp", id="http-mcp")
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", toolsets=[stdio_server, http_server])
        assert integration._get_mcp_servers(agent) == [
            {"name": "stdio-mcp"},
            {"name": "http-mcp", "uri": "https://example.com"},
        ]

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="MCP toolsets verified on pydantic-ai >=1.63.0")
    def test_manifest_mcp_uri_is_redacted(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: the MCP ``uri`` is an allowlist (``scheme://host[:port]`` only).

        An HTTP URL can carry secrets in userinfo (``user:tok@``), the path (``/sk-.../``), the query
        -- under ANY key, allowlisted or not (``?api_key=``, ``?pwd=``, ``?x-api-key=``) -- and the
        fragment (``#token=``). The emitted ``uri`` keeps only scheme + host (+ port), so none of
        those survive. A stdio command emits NO ``uri`` at all (its basename can be a secret). Fails
        if the redactor reverts to a query-key denylist or keeps the path / userinfo / a stdio
        basename.
        """
        mcp = pytest.importorskip("pydantic_ai.mcp")

        integration = pydantic_ai._datadog_integration
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
        # No credential material survives -- userinfo, path secret, NON-allowlist query keys
        # (``pwd``/``x-api-key``), allowlist query key, and fragment are all dropped.
        for secret in ("user:", "tok", "PATHSECRET", "SECRET1", "SECRET2", "sk-secret", "#token", "abc"):
            assert secret not in http_uri, (secret, http_uri)
        # Only the non-secret, identifiable authority is preserved.
        assert http_uri == "https://host.example.com"
        # The stdio server carries NO uri (its command basename could be a secret).
        assert "uri" not in stdio_cap, stdio_cap

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="MCP toolsets verified on pydantic-ai >=1.63.0")
    def test_manifest_mcp_uri_ipv6_and_scheme_less(self, pydantic_ai, pydantic_ai_llmobs):
        """An IPv6 MCP host stays a valid, re-parseable authority and a scheme-less host:port is kept.

        Regression for two redactor edge cases: an IPv6 literal must be re-bracketed (otherwise the
        ``host:port`` colons corrupt the authority), and a scheme-less ``host:port/...`` URL must not
        be mis-routed (it carries a secret query that must still be dropped). Fails if the redactor
        drops the IPv6 brackets or mis-parses the scheme-less authority.
        """
        mcp = pytest.importorskip("pydantic_ai.mcp")
        import urllib.parse

        integration = pydantic_ai._datadog_integration
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
    def test_manifest_mcp_capability_name_is_not_credential_bearing(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: an MCP server with no ``id`` must NOT leak its config through the ``name``.

        ``MCPServer.label`` is a property that returns ``repr(self)`` when ``id`` is unset (the
        default), and that repr embeds the full connection config: an HTTP ``url`` (userinfo + query
        secrets) or a stdio ``command`` + ``args`` (e.g. ``--token=...``). ``_toolset_name`` must read
        only ``id`` (here unset) then fall back to the class name -- never ``label``/repr -- so no
        secret reaches the capability ``name`` (the redacted ``uri`` carries the host identity). Fails
        if ``_toolset_name`` reintroduces the ``label`` rung or any raw ``url`` / ``command`` / ``args``
        read.
        """
        mcp = pytest.importorskip("pydantic_ai.mcp")

        integration = pydantic_ai._datadog_integration
        # Both servers are constructed WITHOUT an ``id`` -- the common case that triggers the leaky
        # ``label`` -> ``repr(self)`` default. The repr would otherwise expose every secret below.
        http_server = mcp.MCPServerStreamableHTTP(url="https://admin:pw123@h.example.com/mcp?api_key=SECRETTOK")
        stdio_server = mcp.MCPServerStdio(command="python", args=["--token=SECRETTOK"])
        # Sanity: the SDK's ``label`` really does leak here -- otherwise this test proves nothing.
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
    def test_manifest_custom_toolset_name_is_not_credential_bearing(self, pydantic_ai, pydantic_ai_llmobs):
        """A custom toolset whose ``repr`` embeds config surfaces only its class name as ``name``.

        ``_get_custom_toolsets`` names a toolset via ``_toolset_name`` (``id`` then class name), so a
        leaky ``__repr__`` (which ``label``/repr-based naming would have surfaced) never reaches the
        ``custom`` capability ``name``. Fails if ``_toolset_name`` reintroduces a ``label`` / repr read.
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
        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", toolsets=[leaky])

        custom = integration._get_custom_toolsets(agent)
        assert custom == [{"name": "LeakyToolset"}], custom
        for secret in ("AKIA_CUSTOMSECRET", "hunter2"):
            assert secret not in custom[0]["name"], (secret, custom)

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output_type verified on pydantic-ai >=1.63.0")
    def test_manifest_output_type_schema_is_bounded(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: an oversized output-type schema is dropped (name kept) rather than emitted.

        A model whose JSON schema exceeds the byte budget must not be serialized onto the span (it
        could embed PII in ``Field`` metadata and evict real I/O under the size cap). The ``name`` is
        always kept and ``schema_truncated`` flags the drop; a small model still carries its schema.
        Fails if ``_get_agent_output_type`` stores ``model_json_schema()`` unbounded.
        """
        from pydantic import BaseModel
        from pydantic import Field

        integration = pydantic_ai._datadog_integration

        # ~200 long-described fields -> serialized schema well over the 8 KB budget.
        fields = {f"field_{i}": (str, Field(description="d" * 120, examples=["pii@example.com"])) for i in range(200)}
        Big = type(
            "Big",
            (BaseModel,),
            {"__annotations__": {k: t for k, (t, _) in fields.items()}, **{k: f for k, (_, f) in fields.items()}},
        )

        class Small(BaseModel):
            city: str

        big_output = integration._get_agent_output_type(pydantic_ai.Agent(model="gpt-4o", name="big", output_type=Big))
        assert big_output["name"] == "Big"
        assert "schema" not in big_output
        assert big_output["schema_truncated"] is True

        small_output = integration._get_agent_output_type(
            pydantic_ai.Agent(model="gpt-4o", name="small", output_type=Small)
        )
        assert small_output["name"] == "Small"
        assert small_output["schema"]["properties"].keys() == {"city"}
        assert "schema_truncated" not in small_output

    @pytest.mark.skipif(
        PYDANTIC_AI_VERSION < (1, 63, 0), reason="sub-agent introspection verified on pydantic-ai >=1.63.0"
    )
    def test_manifest_sub_agent_capability(self, pydantic_ai, pydantic_ai_llmobs):
        """A function tool that delegates to another Agent is captured as a ``sub_agent`` capability.

        Driven through the builder (not a live run) so no model call is needed. ``sub_worker`` is
        closure-captured by ``delegate_to_sub_agent``, which the bytecode walk resolves. Plain function
        tools (``calculate_square_tool``) are ``type: function`` in the same unified ``capabilities`` list;
        a delegating tool is single-homed as ``type: sub_agent`` (carrying its parameters + target agent).
        """
        integration = pydantic_ai._datadog_integration
        sub_worker = pydantic_ai.Agent(model="gpt-4o", name="sub_worker")

        def delegate_to_sub_agent(query: str) -> str:
            """Delegate the query to the sub agent."""
            return sub_worker.run_sync(query).output

        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", tools=[calculate_square_tool, delegate_to_sub_agent]
        )
        # Both tools stay in the frozen ``tools`` list (registration order); the delegating one is
        # ADDITIONALLY surfaced in ``capabilities`` as a ``sub_agent`` -- it is NOT removed from ``tools``.
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
        # In the unified ``capabilities`` superset the delegating tool is single-homed as ``sub_agent``
        # (carrying its parameter schema + resolved target agent); the plain tool stays ``type: tool``.
        capabilities = {c["name"]: c for c in integration._build_capabilities(agent)}
        assert capabilities["delegate_to_sub_agent"] == {
            "name": "delegate_to_sub_agent",
            "type": "sub_agent",
            "content": {"schema": {"query": {"type": "string", "required": True}}, "agent": "sub_worker"},
            "description": "Delegate the query to the sub agent.",
        }
        assert capabilities["calculate_square_tool"]["type"] == "tool"

    @pytest.mark.skipif(
        PYDANTIC_AI_VERSION < (1, 63, 0), reason="sub-agent introspection verified on pydantic-ai >=1.63.0"
    )
    def test_manifest_agent_reference_without_delegation_stays_tool(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: a tool that REFERENCES an ``Agent`` but does not delegate stays ``type: tool``.

        ``_referenced_agent_name`` matches any Agent in the tool's globals/closure, so a tool that merely
        reads ``agent.name`` (or references an Agent for logging) would be mislabeled ``sub_agent`` and
        corrupt the versioning manifest. ``_build_capabilities`` gates the reclassification on
        ``_fn_delegates`` -- a delegation method (``run`` / ``run_sync`` / ``run_stream`` / ``iter``) must
        appear in the tool's code. A real delegating tool stays ``sub_agent`` (see
        ``test_manifest_sub_agent_capability``). Fails-on-revert: drop the gate and this tool flips to
        ``sub_agent``.
        """
        integration = pydantic_ai._datadog_integration
        sub_worker = pydantic_ai.Agent(model="gpt-4o", name="sub_worker")

        def reads_agent_name(query: str) -> str:
            """References an Agent but only reads its name -- does not delegate."""
            return f"{sub_worker.name}: {query}"

        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", tools=[reads_agent_name])
        capabilities = {c["name"]: c for c in integration._build_capabilities(agent)}
        assert capabilities["reads_agent_name"]["type"] == "tool", capabilities
        assert "agent" not in capabilities["reads_agent_name"]["content"], capabilities
        # The discriminator itself: reading ``.name`` is not delegation; a ``run_sync`` call is.
        assert integration._fn_delegates(reads_agent_name) is False

        def delegates(query: str) -> str:
            """Delegates to the sub agent."""
            return sub_worker.run_sync(query).output

        assert integration._fn_delegates(delegates) is True

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="custom toolsets verified on pydantic-ai >=1.63.0")
    def test_manifest_custom_toolset_tools_not_over_captured_as_function_caps(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: a custom (non-Function) toolset surfaces ONLY as a ``custom`` capability.

        ``_iter_agent_tools`` reads ``.tools`` only from ``FunctionToolset``s now; a custom toolset (whose
        ``.tools`` may be absent or a non-dict) is excluded, so its entries are neither double-counted as
        function ``tool`` caps nor able to crash the manifest build on ``.items()`` -- which, since the
        manifest is built inside ``_llmobs_set_tags_agent``, would otherwise blank the whole agent span.
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

        integration = pydantic_ai._datadog_integration
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
    def test_manifest_handoff_from_output_function(self, pydantic_ai, pydantic_ai_llmobs):
        """An output-function callable that delegates to an Agent is captured as a ``handoff``.

        The callable is partitioned out of ``output_type`` (which is empty here) and into
        ``handoffs`` with its docstring as the description and the resolved sub-agent name.
        """
        integration = pydantic_ai._datadog_integration
        sub_worker = pydantic_ai.Agent(model="gpt-4o", name="sub_worker")

        def route_to_sub_agent(ctx, text: str) -> str:
            """Route the request to the sub agent."""
            return sub_worker.run_sync(text).output

        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=route_to_sub_agent)
        assert integration._get_agent_output_type(agent) == {}
        assert integration._get_agent_handoffs(agent) == [
            {
                "name": "route_to_sub_agent",
                "description": "Route the request to the sub agent.",
                "agent": "sub_worker",
            }
        ]

    @pytest.mark.skipif(
        PYDANTIC_AI_VERSION < (1, 63, 0), reason="output-function handoffs verified on pydantic-ai >=1.63.0"
    )
    def test_manifest_handoff_description_is_bounded(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: an output-function handoff's ``description`` is byte-capped like every other captured
        text field.

        The handoff ``description`` (marker ``description`` or the function docstring) rides every agent span;
        an oversized one must be dropped and flagged ``description_truncated`` rather than emitted, or it
        could push the event over the size limit and evict the user's real I/O. A small description is kept.
        Fails-on-revert: drop the cap and the oversized description is emitted in full.
        """
        integration = pydantic_ai._datadog_integration

        def big_handoff(text: str) -> str:
            return text

        big_handoff.__doc__ = "d" * (_PROMPT_SOURCE_MAX_BYTES + 1)
        big = integration._get_agent_handoffs(pydantic_ai.Agent(model="gpt-4o", name="a", output_type=big_handoff))
        assert len(big) == 1
        assert big[0]["description_truncated"] is True
        assert "description" not in big[0]

        def small_handoff(text: str) -> str:
            """Route to the sub agent."""
            return text

        small = integration._get_agent_handoffs(pydantic_ai.Agent(model="gpt-4o", name="a", output_type=small_handoff))
        assert small[0]["description"] == "Route to the sub agent."
        assert "description_truncated" not in small[0]

    def test_manifest_build_failure_does_not_blank_agent_span(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: a manifest-build exception degrades to no-manifest, NOT a blanked agent span.

        ``_build_agent_manifest`` does a lot (closure walks, ``inspect.getsource``, ``model_json_schema``), so
        it can raise. It is now called AFTER the name/input/output annotation in ``_llmobs_set_tags_agent``,
        so those survive even when the manifest raises (``llmobs_set_tags`` logs and bails on the raise, so
        anything annotated before it persists). Fails-on-revert: move the ``_tag_agent_manifest`` call back
        above the annotation and the name/input/output are lost when the manifest raises.
        """
        integration = pydantic_ai._datadog_integration
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
    def test_manifest_tool_output_marker_keeps_schema(self, pydantic_ai, pydantic_ai_llmobs):
        """A ``ToolOutput`` wrapping a Pydantic model keeps its schema and emits no handoff."""
        from pydantic import BaseModel
        from pydantic_ai.output import ToolOutput

        class Weather(BaseModel):
            city: str

        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=ToolOutput(Weather))
        output_type = integration._get_agent_output_type(agent)
        assert output_type["name"] == "Weather"
        assert output_type["schema"]["properties"].keys() == {"city"}
        assert integration._get_agent_handoffs(agent) == []

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output markers verified on pydantic-ai >=1.63.0")
    def test_manifest_output_marker_functions_route_to_handoffs(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: ``NativeOutput`` / ``PromptedOutput`` / ``TextOutput`` wrapping a callable.

        Each wraps the target under a different attribute (``.outputs`` / ``.outputs`` /
        ``.output_function``) than ``ToolOutput`` (``.output``). The callable must land in
        ``handoffs`` and ``output_type`` must NOT be a ``repr``-of-marker string (which embeds a
        non-deterministic memory address). Fails if the marker unwrap only reads ``.output``.
        """
        from pydantic_ai.output import NativeOutput
        from pydantic_ai.output import PromptedOutput
        from pydantic_ai.output import TextOutput

        integration = pydantic_ai._datadog_integration
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
            assert [h["name"] for h in handoffs] == ["route"], (marker_cls.__name__, handoffs)
            assert handoffs[0].get("agent") == "sub_worker", (marker_cls.__name__, handoffs)
            # No memory-address string leaks onto the span via output_type.
            assert "0x" not in safe_json(output_type), (marker_cls.__name__, output_type)

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output markers verified on pydantic-ai >=1.63.0")
    def test_manifest_output_marker_model_keeps_schema(self, pydantic_ai, pydantic_ai_llmobs):
        """``NativeOutput`` / ``PromptedOutput`` wrapping a model keep the schema (no handoff).

        Confirms the unwrap reads ``.outputs`` for the structured (model) case too -- otherwise the
        model would be dropped from ``output_type`` entirely.
        """
        from pydantic import BaseModel
        from pydantic_ai.output import NativeOutput
        from pydantic_ai.output import PromptedOutput

        class Weather(BaseModel):
            city: str

        integration = pydantic_ai._datadog_integration
        for marker_cls in (NativeOutput, PromptedOutput):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", output_type=marker_cls(Weather))
            output_type = integration._get_agent_output_type(agent)
            assert output_type["name"] == "Weather", (marker_cls.__name__, output_type)
            assert output_type["schema"]["properties"].keys() == {"city"}, marker_cls.__name__
            assert integration._get_agent_handoffs(agent) == [], marker_cls.__name__

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="output_type verified on pydantic-ai >=1.63.0")
    def test_manifest_dataclass_output_type_with_marker_field_names(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: a non-marker ``output_type`` whose members collide with marker attr names.

        ``ToolOutput``/``NativeOutput``/``PromptedOutput``/``TextOutput`` wrap their target under
        ``output`` / ``outputs`` / ``output_function``. Those names are NOT exclusive to the markers,
        so a plain dataclass/NamedTuple/Enum with a member of the same name was duck-typed as a
        marker -- emitting the field DEFAULT (or a descriptor repr / ``Enum.member``) as the type
        ``name`` and losing the class name, or fabricating a handoff from a callable default and a
        ``description`` field. Fails if ``_marker_target`` reads the attr before an ``isinstance``
        gate. ``_get_agent_output_type`` must name the class itself and emit no handoff.
        """
        from dataclasses import dataclass
        from enum import Enum
        from typing import NamedTuple

        integration = pydantic_ai._datadog_integration

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
        assert len(cases) == 4
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
        """A dynamic (callable) instructions function is captured in the additive ``extra_instructions``
        list as a ``{type: dynamic_instructions, content: {...}}`` entry whose ``content`` is a structured
        descriptor (name / signature / doc / source / reevaluated); the frozen ``instructions`` key stays
        a string|None (here None -- no static text).
        """
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")

            @agent.instructions
            def dynamic_instructions(ctx) -> str:
                """Resolved per run."""
                return "computed instructions"

            result = await agent.run("Hello, world!")
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        span_data = _get_llmobs_data_metastruct(spans[0])
        assert_llmobs_span_data(
            span_data,
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            tags=PYDANTIC_AI_TAGS,
        )
        manifest = span_data["meta"]["metadata"]["_dd"]["agent_manifest"]
        entries = [e for e in manifest["extra_instructions"] if e["type"] == "dynamic_instructions"]
        assert len(entries) == 1
        descriptor = entries[0]["content"]
        assert descriptor["name"] == "dynamic_instructions"
        assert descriptor["signature"] == "(ctx) -> str"
        assert descriptor["doc"] == "Resolved per run."
        assert descriptor["reevaluated"] is True
        # ``source`` is the full definition (asserted by substring, not byte-for-byte, so the test does
        # not break on indentation); ``getsource`` includes the ``@agent.instructions`` decorator line.
        assert "def dynamic_instructions(ctx) -> str:" in descriptor["source"]
        assert "@agent.instructions" in descriptor["source"]
        # A dynamic-only agent's frozen ``instructions`` key is None (no static text).
        assert manifest["instructions"] is None

    def test_manifest_restores_system_prompts_and_omits_dependencies(self, pydantic_ai, pydantic_ai_llmobs):
        """A static ``system_prompt`` is captured in the ``system_prompts`` list, while a ``dependencies``
        field is not emitted -- the dependency type surfaces under ``settings.deps_type`` instead.
        """
        integration = pydantic_ai._datadog_integration

        class SupportDeps:
            pass

        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", system_prompt="you are a bot", deps_type=SupportDeps
        )
        manifest = integration._build_agent_manifest(agent)
        # system_prompts is restored as the frozen LIST (static-only -> no extra_instructions key).
        assert list(manifest["system_prompts"]) == ["you are a bot"]
        assert "extra_instructions" not in manifest
        # The removed dependencies block stays gone; deps_type surfaces under the flat settings.
        assert "dependencies" not in manifest
        assert manifest["settings"].get("deps_type") == "SupportDeps"

    def test_manifest_captures_dynamic_instructions_all_versions(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression (whole matrix): a dynamic ``@agent.instructions`` function is captured on EVERY
        pydantic-ai version.

        This is the load-bearing version-drift test. On >=1.63.0 the callable lives in the
        ``agent._instructions`` list; on <1.63.0 it lives in ``agent._instructions_functions`` (a
        ``SystemPromptRunner`` the collector unwraps via ``.function``). The pre-enrichment code read only
        ``agent._instructions``, so it silently missed the function on 0.8.1 / 1.0.0. Builder-driven (no
        cassette) so it runs on the full pin matrix. Fails if the collector stops reading
        ``_instructions_functions``.
        """
        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")

        @agent.instructions
        def dynamic_instructions(ctx) -> str:
            """Resolved per run."""
            return "computed instructions"

        manifest = integration._build_agent_manifest(agent)
        entries = [e for e in manifest["extra_instructions"] if e["type"] == "dynamic_instructions"]
        assert len(entries) == 1
        descriptor = entries[0]["content"]
        assert descriptor["name"] == "dynamic_instructions"
        assert descriptor["signature"] == "(ctx) -> str"
        assert descriptor["doc"] == "Resolved per run."
        assert "def dynamic_instructions(ctx) -> str:" in descriptor["source"]
        # A dynamic-only agent's frozen ``instructions`` key is None (no static text).
        assert manifest["instructions"] is None

    def test_manifest_captures_static_and_dynamic_system_prompts(self, pydantic_ai, pydantic_ai_llmobs):
        """A static ``system_prompt`` plus a dynamic ``@agent.system_prompt`` function are BOTH captured.

        Builder-driven (runs on every pin -- ``_system_prompts`` / ``_system_prompt_functions`` are stable
        across versions). The static string lands in the frozen ``system_prompts`` list and the dynamic
        function is described as a ``{type: dynamic_system_prompt, content: {...}}`` entry in the additive
        ``extra_instructions`` list (carrying ``reevaluated``) -- single-homed, so the source is not
        mirrored into the static string.
        """
        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", system_prompt="You are a bot.")

        @agent.system_prompt
        def dynamic_system_prompt(ctx) -> str:
            """Adds the user name."""
            return "computed system prompt"

        manifest = integration._build_agent_manifest(agent)
        # The frozen ``system_prompts`` LIST holds the static string ONLY; the dynamic function is in the
        # additive ``extra_instructions`` list, typed ``dynamic_system_prompt``.
        assert list(manifest["system_prompts"]) == ["You are a bot."]
        entries = [e for e in manifest["extra_instructions"] if e["type"] == "dynamic_system_prompt"]
        assert len(entries) == 1
        descriptor = entries[0]["content"]
        assert descriptor["name"] == "dynamic_system_prompt"
        assert descriptor["signature"] == "(ctx) -> str"
        assert descriptor["doc"] == "Adds the user name."
        assert "@agent.system_prompt" in descriptor["source"]
        # A plain ``@agent.system_prompt`` runs once (not re-evaluated each step).
        assert descriptor["reevaluated"] is False
        # The dynamic source is NOT mirrored into the static system_prompts.
        assert "def dynamic_system_prompt" not in " ".join(manifest["system_prompts"])

    def test_manifest_prompt_source_is_bounded(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: a dynamic-prompt source over the byte budget is dropped (flagged) rather than emitted.

        A prompt function whose source exceeds the byte budget must not be serialized onto the span (it
        rides every agent span and would evict real I/O under the size cap). The descriptor keeps the
        ``name`` and flags ``source_truncated``; a small function still carries its source. Analog of
        ``test_manifest_output_type_schema_is_bounded``. Fails if ``_describe_prompt_functions`` stores the
        source unbounded.

        ``inspect.getsource`` is patched to RETURN an over-budget string (rather than the no-source /
        raise case, which ``test_manifest_prompt_source_unavailable_flags_truncated`` covers) so this test
        exercises the byte-cap branch specifically -- it would fail if the ``_PROMPT_SOURCE_MAX_BYTES``
        length check were removed.
        """
        integration = pydantic_ai._datadog_integration

        def dynamic_instructions(ctx) -> str:
            """Resolved per run."""
            return "x"

        oversized_source = "x" * (_PROMPT_SOURCE_MAX_BYTES + 1)
        with mock.patch("ddtrace.llmobs._integrations.pydantic_ai.inspect.getsource", return_value=oversized_source):
            big_described = integration._describe_prompt_functions([dynamic_instructions])
        assert len(big_described) == 1
        assert big_described[0]["name"] == "dynamic_instructions"
        assert "source" not in big_described[0]
        assert big_described[0]["source_truncated"] is True

        # Unpatched, the small real function's source is captured (under budget, no truncation flag).
        def small_dynamic(ctx) -> str:
            """Small."""
            return "x"

        small_described = integration._describe_prompt_functions([small_dynamic])
        assert small_described[0]["name"] == "small_dynamic"
        assert "def small_dynamic(ctx) -> str:" in small_described[0]["source"]
        assert "source_truncated" not in small_described[0]

    def test_manifest_prompt_source_unavailable_flags_truncated(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: a prompt function with no retrievable source flags ``source_truncated``.

        ``inspect.getsource`` raises ``OSError`` (source file gone / REPL-defined) or ``TypeError``
        (C-implemented / builtin) for functions whose text cannot be read; a lambda from a live REPL is the
        canonical case. The descriptor must still emit the ``name`` / ``signature`` and flag
        ``source_truncated`` -- never raise and never emit a partial ``source``. Fails if the ``getsource``
        guard is removed.
        """
        integration = pydantic_ai._datadog_integration

        def dynamic_instructions(ctx) -> str:
            """Resolved per run."""
            return "x"

        with mock.patch("ddtrace.llmobs._integrations.pydantic_ai.inspect.getsource", side_effect=OSError("no source")):
            described = integration._describe_prompt_functions([dynamic_instructions])
        assert len(described) == 1
        descriptor = described[0]
        assert descriptor["name"] == "dynamic_instructions"
        assert descriptor["signature"] == "(ctx) -> str"
        assert descriptor["doc"] == "Resolved per run."
        assert descriptor["source_truncated"] is True
        assert "source" not in descriptor

    @pytest.mark.skipif(
        PYDANTIC_AI_VERSION < (1, 63, 0), reason="mixed _instructions list shape verified on pydantic-ai >=1.63.0"
    )
    def test_manifest_mixed_instructions_list_splits_static_and_dynamic(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression (>=1.63.0 only): ``agent._instructions`` is a list mixing a static string with a raw
        callable; the collector splits them and de-dupes the callable.

        On >=1.63.0 the dynamic function is stored directly in the ``_instructions`` list (not in
        ``_instructions_functions``, which is absent). The static string must land in the primary
        ``instructions`` text and the callable must be described exactly once. No current SDK version
        dual-stores a dynamic instruction, so the ``id``-based de-dupe is defensive; fails if the list-split breaks.
        """
        integration = pydantic_ai._datadog_integration
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
        entries = [e for e in manifest["extra_instructions"] if e["type"] == "dynamic_instructions"]
        assert len(entries) == 1
        assert entries[0]["content"]["name"] == "dynamic_instructions"

    def test_manifest_agent_settings_retries_version_tolerant(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: ``retries`` survives the post-1.63.0 rename of ``_max_result_retries``.

        pydantic-ai <=1.63.0 stores the output-retry budget on ``_max_result_retries``; >1.63.0
        removed that name and stores it on ``_max_output_retries`` (verified on 1.107.0). Runs on
        every pin: construct ``retries=2`` (both names, where present, are 2), then simulate the
        post-rename shape by removing ``_max_result_retries`` while leaving ``_max_output_retries``
        set, and assert the builder still emits ``retries == 2``. Fails against an
        ``_max_result_retries``-only read (the key would silently vanish on >1.63.0). ``retries`` is the
        OUTPUT budget; the distinct per-tool budget ``_max_tool_retries`` is emitted separately as
        ``tool_retries`` -- a divergent value is set here to prove the two are never crossed.
        """
        integration = pydantic_ai._datadog_integration
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
        settings = manifest["settings"]
        assert settings["retries"] == 2, settings
        # The per-tool budget is emitted separately as ``tool_retries`` and is NOT crossed with output.
        assert settings["tool_retries"] == 7, settings

    def test_manifest_top_level_shape(self, pydantic_ai, pydantic_ai_llmobs):
        """The manifest's top-level shape is flat: identity + model + ``instructions`` (str) / ``tools``
        (list) + the ``capabilities`` list + ``output_type`` + a flat ``settings`` -- no grouped
        ``behaviors`` key and no ``definition`` wrapper. Builder-driven.
        """
        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", instructions="You are helpful.", tools=[calculate_square_tool]
        )
        manifest = integration._build_agent_manifest(agent)

        assert manifest["framework"] == "PydanticAI"
        assert manifest["name"] == "test_agent"
        assert manifest["model"] == "gpt-4o"
        assert manifest["model_provider"] == "openai"
        # Frozen: ``instructions`` is a plain string; ``tools`` is the flat back-compat list -- its entries
        # carry NO ``type`` key (that lives on the ``capabilities`` envelope).
        assert manifest["instructions"] == "You are helpful."
        assert [t["name"] for t in manifest["tools"]] == ["calculate_square_tool"]
        assert "type" not in manifest["tools"][0]
        # The function tool is ADDITIONALLY surfaced in the unified ``capabilities`` superset, typed.
        assert [(c["name"], c["type"]) for c in manifest["capabilities"]] == [("calculate_square_tool", "tool")]
        assert manifest["output_type"] == {"name": "str"}
        # ``settings`` is a flat top-level key (no ``behaviors`` grouping); retry budgets are 1/1 on every
        # supported pin (``end_strategy`` / ``deps_type`` defaults vary by version -- only assert shape).
        assert "behaviors" not in manifest
        settings = manifest["settings"]
        assert settings["retries"] == 1
        assert settings["tool_retries"] == 1
        assert "end_strategy" in settings
        # No legacy ``definition`` wrapper.
        assert "definition" not in manifest

    def test_manifest_additive_over_shipped_contract(self, pydantic_ai, pydantic_ai_llmobs):
        """The manifest is additive over the previously shipped manifest -- the shipped ``instructions``
        (str|None) / ``system_prompts`` (list/tuple) / ``tools`` (flat list, always present) keep their
        exact name + type, so existing consumers see no change. The ``capabilities`` list is a sibling of
        the flat ``tools`` list (it never replaces it), and no grouped ``behaviors`` key is emitted.
        Builder-driven so it runs on every pin; checks a minimal and a rich agent.
        """
        from pydantic import BaseModel

        integration = pydantic_ai._datadog_integration

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

    def test_manifest_captures_guardrails(self, pydantic_ai, pydantic_ai_llmobs):
        """``@agent.output_validator`` functions are captured as ``guardrails`` with full descriptors
        (name / signature / doc / source), so a change to a validator is detectable.

        Output validators run as a chained pipeline (each receives the previous validator's output), so
        their order is semantic and is preserved as registered -- not sorted.
        """
        integration = pydantic_ai._datadog_integration
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
        assert guardrails["reject_ungrounded"]["doc"] == "Reject ungrounded output."
        assert "def strip_pii(ctx, output):" in guardrails["strip_pii"]["source"]

    def test_manifest_guardrails_order_is_preserved(self, pydantic_ai, pydantic_ai_llmobs):
        """Output validators chain (each sees the prior's output), so guardrail order is SEMANTIC and must
        NOT be sorted: registering in reversed order yields a different manifest. Fails-on-revert if the
        guardrails list is sorted.
        """
        integration = pydantic_ai._datadog_integration

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

    def test_manifest_captures_tool_transforms(self, pydantic_ai, pydantic_ai_llmobs):
        """``prepare_tools`` / ``prepare_output_tools`` are captured as ``tool_transforms`` with a ``scope``
        (they rewrite the tool set per run, so they are part of the definition).
        """
        integration = pydantic_ai._datadog_integration

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

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="pydantic-ai < 1.63.0 has no Agent metadata")
    def test_manifest_metadata_captured(self, pydantic_ai, pydantic_ai_llmobs):
        """Display-only ``metadata`` is captured as a flat top-level field."""
        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", metadata={"team": "billing"})
        manifest = integration._build_agent_manifest(agent)
        assert manifest["metadata"] == {"team": "billing"}

    def test_manifest_history_processors_captured(self, pydantic_ai, pydantic_ai_llmobs):
        """An agent's ``history_processors`` (its memory / history policy) are captured under the flat
        ``memory_policies`` key (the canonical cross-framework name) and described like any prompt function.

        Builder-driven: ``agent.history_processors`` is a public list of bare callables on every supported
        version (0.8.1 / 1.0.0 / 1.63.0), so no cassette and no ``.function`` unwrap are needed.
        """
        integration = pydantic_ai._datadog_integration

        def keep_last_message(messages):
            """Trim history to the most recent message."""
            return messages[-1:]

        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", history_processors=[keep_last_message])
        processors = integration._build_agent_manifest(agent)["memory_policies"]
        assert len(processors) == 1
        assert processors[0]["name"] == "keep_last_message"
        assert processors[0]["doc"] == "Trim history to the most recent message."
        assert "def keep_last_message(messages):" in processors[0]["source"]

    def test_manifest_history_processors_omitted_when_absent(self, pydantic_ai, pydantic_ai_llmobs):
        """An agent with no history processors emits no ``memory_policies`` key."""
        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
        assert "memory_policies" not in integration._build_agent_manifest(agent)

    def test_manifest_history_processors_order_is_preserved(self, pydantic_ai, pydantic_ai_llmobs):
        """History processors run as a pipeline, so their order is SEMANTIC and must NOT be sorted:
        reversing the registration order yields a different manifest (unlike ``tools``).
        """
        integration = pydantic_ai._datadog_integration

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

    def test_manifest_tools_preserve_registration_order(self, pydantic_ai, pydantic_ai_llmobs):
        """The ``tools`` list preserves registration order -- it is NOT sorted. Two agents registering the
        same tools in reversed order therefore produce different ``tools`` orderings. (Order-incidental
        siblings like ``capabilities`` are sorted via ``_sorted_definition_list``; ``tools`` is not.)
        Builder-driven.
        """
        integration = pydantic_ai._datadog_integration
        manifest_ab = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", tools=[calculate_square_tool, foo_tool])
        )
        manifest_ba = integration._build_agent_manifest(
            pydantic_ai.Agent(model="gpt-4o", name="test_agent", tools=[foo_tool, calculate_square_tool])
        )
        assert [t["name"] for t in manifest_ab["tools"]] == ["calculate_square_tool", "foo_tool"]
        assert [t["name"] for t in manifest_ba["tools"]] == ["foo_tool", "calculate_square_tool"]

    def test_sorted_definition_list_total_order_breaks_ties(self, pydantic_ai, pydantic_ai_llmobs):
        """Order-incidental lists sort by a primary field WITH a full-content tiebreaker, so entries that
        tie on the primary field but differ in content (e.g. two MCP servers with the same name but a
        different URL) still order deterministically -- reversal-invariant on the wire.
        """
        from ddtrace.llmobs._integrations.pydantic_ai import _sorted_definition_list

        a = {"kind": "mcp", "name": "srv", "uri": "https://a"}
        b = {"kind": "mcp", "name": "srv", "uri": "https://b"}  # ties on (kind, name); differs in uri
        assert safe_json(_sorted_definition_list([a, b], ("kind", "name"))) == safe_json(
            _sorted_definition_list([b, a], ("kind", "name"))
        )
        # A single-field key with a tie is likewise broken by content.
        c = {"tool_name": "route", "handoff_description": "to A"}
        d = {"tool_name": "route", "handoff_description": "to B"}
        assert safe_json(_sorted_definition_list([c, d], ("tool_name",))) == safe_json(
            _sorted_definition_list([d, c], ("tool_name",))
        )

    async def test_agent_run_stream(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream(debounce_by=None):
                    output = chunk
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.parametrize("delta", [False, True])
    async def test_agent_run_stream_text(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, delta):
        """
        delta determines whether each chunk represents the entire output up to the current point or just the
        delta from the previous chunk
        """
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                async for chunk in result.stream_text(debounce_by=None, delta=delta):
                    output = output + chunk if delta else chunk
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_get_output(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream("Hello, world!") as result:
                output = await result.get_output()
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
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
        trace = test_spans.pop_traces()[0]
        agent_span_data = _get_llmobs_data_metastruct(trace[0])
        tool_span_data = _get_llmobs_data_metastruct(trace[1])
        assert_llmobs_span_data(
            agent_span_data,
            span_kind="agent",
            name="test_agent",
            input_value="What is the square of 2?",
            output_value=output,
            metadata=expected_agent_metadata(instructions=instructions, tools=expected_calculate_square_tool()),
            tags=PYDANTIC_AI_TAGS,
        )
        assert_llmobs_span_data(
            tool_span_data,
            span_kind="tool",
            name="calculate_square_tool",
            parent_id=str(trace[0].span_id),
            input_value='{"x":2}',
            output_value="4",
            metadata=TOOL_DESCRIPTION_METADATA,
            tags=PYDANTIC_AI_TAGS,
        )

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
        trace = test_spans.pop_traces()[0]
        agent_span_data = _get_llmobs_data_metastruct(trace[0])
        tool_span_data = _get_llmobs_data_metastruct(trace[1])
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
        assert_llmobs_span_data(
            tool_span_data,
            span_kind="tool",
            name="calculate_square_tool",
            parent_id=str(trace[0].span_id),
            input_value='{"x":2}',
            output_value="4",
            metadata=TOOL_DESCRIPTION_METADATA,
            tags=PYDANTIC_AI_TAGS,
        )

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

        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(tools=expected_calculate_square_tool() + expected_foo_tool()),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.parametrize("invoke", ["run", "run_stream", "iter"])
    async def test_agent_message_history_sets_input_value(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans, invoke
    ):
        """INPUT_VALUE is taken from message_history when no user_prompt is passed -- across run /
        run_stream / iter. Each extracts output differently, but the input-from-history behavior is shared.
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello from history!",
            output_value=output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello, world!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_with_unserializable_model_settings(self, pydantic_ai, pydantic_ai_llmobs, test_spans):
        """Regression test: agent.model_settings containing non-JSON-serializable provider
        sentinel values must not crash span submission.

        Uses FunctionModel to avoid OpenAI SDK serialization, which would reject the
        sentinel before our span-tagging code ever runs.
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
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        span_data = _get_llmobs_data_metastruct(spans[0])
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
    """Regression test: ``agent.model_settings`` may hold provider sentinels (e.g.
    OpenAI's ``Omit``/``NOT_GIVEN`` for unset params). The agent manifest is written to
    the span's meta_struct, so storing these raw crashed the agentless trace encoder at
    span finish (``TypeError: Object of type Omit is not JSON serializable``). They must
    be coerced to JSON-safe values while serializable settings are preserved.
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
