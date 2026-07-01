import mock
import pydantic_ai
import pytest
from typing_extensions import TypedDict

from ddtrace.internal.utils.version import parse_version
from ddtrace.llmobs._integrations.pydantic_ai import _METADATA_MAX_BYTES
from ddtrace.llmobs._integrations.pydantic_ai import _PROMPT_SOURCE_MAX_BYTES
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
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
                # The agent sets a static ``system_prompt`` -> the manifest now carries ``system_prompts``.
                system_prompts=[system_prompt],
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
        """The dependency-injection deps type is captured into ``manifest.agent_settings.deps_type``."""

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
                agent_settings={"retries": 1, "end_strategy": "early", "deps_type": "SupportDeps"},
            ),
            tags=PYDANTIC_AI_TAGS,
        )

    @pytest.mark.skipif(PYDANTIC_AI_VERSION < (1, 63, 0), reason="builtin tool kinds verified on pydantic-ai >=1.63.0")
    def test_agent_run_with_builtin_capability(self, pydantic_ai, pydantic_ai_llmobs):
        """A builtin tool is captured as a typed ``builtin`` capability entry.

        Built directly from the constructed agent (no run): a provider-hosted builtin like
        ``WebSearchTool`` is model-gated at run time (``OpenAIChatModel`` rejects it), but the manifest
        reads only the configured ``_builtin_tools``, so a run is unnecessary and version-fragile.
        """
        from pydantic_ai.builtin_tools import WebSearchTool

        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", builtin_tools=[WebSearchTool()])
        capabilities = integration._get_agent_capabilities(agent)
        assert {"kind": "builtin", "name": "web_search", "provider": "pydantic_ai"} in capabilities

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
        assert integration._get_agent_capabilities(agent) == [
            {"kind": "mcp", "name": "stdio-mcp"},
            {"kind": "mcp", "name": "http-mcp", "uri": "https://example.com"},
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
        capabilities = integration._get_agent_capabilities(agent)
        assert len(capabilities) == 2
        http_cap, stdio_cap = capabilities
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
        capabilities = integration._get_agent_capabilities(agent)
        ipv6_uri = capabilities[0]["uri"]
        bare_uri = capabilities[1]["uri"]
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

        capabilities = integration._get_agent_capabilities(agent)
        assert len(capabilities) == 2
        for cap in capabilities:
            for secret in ("pw123", "SECRETTOK", "admin:"):
                assert secret not in cap["name"], (secret, cap)
        # The ``name`` falls back to the class name (the only safe, constant handle).
        names = {cap["name"] for cap in capabilities}
        assert names == {"MCPServerStreamableHTTP", "MCPServerStdio"}, capabilities
        # The HTTP server still carries the redacted host; the stdio server carries no uri.
        http_cap = next(c for c in capabilities if c["name"] == "MCPServerStreamableHTTP")
        stdio_cap = next(c for c in capabilities if c["name"] == "MCPServerStdio")
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

        capabilities = integration._get_agent_capabilities(agent)
        assert capabilities == [{"kind": "custom", "name": "LeakyToolset"}], capabilities
        for secret in ("AKIA_CUSTOMSECRET", "hunter2"):
            assert secret not in capabilities[0]["name"], (secret, capabilities)

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
        """A function tool that delegates to another Agent is captured as a ``sub-agent``.

        Driven through the builder (not a live run) so no model call is needed. ``sub_worker`` is
        closure-captured by ``delegate_to_sub_agent``, which the bytecode walk resolves;
        ``calculate_square_tool`` is a plain tool and must NOT appear as a capability (function tools
        stay in ``manifest["tools"]``).
        """
        integration = pydantic_ai._datadog_integration
        sub_worker = pydantic_ai.Agent(model="gpt-4o", name="sub_worker")

        def delegate_to_sub_agent(query: str) -> str:
            """Delegate the query to the sub agent."""
            return sub_worker.run_sync(query).output

        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", tools=[calculate_square_tool, delegate_to_sub_agent]
        )
        assert integration._get_agent_capabilities(agent) == [
            {"kind": "sub-agent", "name": "delegate_to_sub_agent", "agent_name": "sub_worker"}
        ]

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
                "tool_name": "route_to_sub_agent",
                "handoff_description": "Route the request to the sub agent.",
                "agent_name": "sub_worker",
            }
        ]

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
            assert [h["tool_name"] for h in handoffs] == ["route"], (marker_cls.__name__, handoffs)
            assert handoffs[0].get("agent_name") == "sub_worker", (marker_cls.__name__, handoffs)
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
        """A dynamic (callable) instructions function flips ``instructions_is_dynamic`` and is captured as a
        structured ``instructions_functions`` descriptor (name / signature / doc / source).
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
        assert manifest["instructions_is_dynamic"] is True
        functions = manifest["instructions_functions"]
        assert len(functions) == 1
        descriptor = functions[0]
        assert descriptor["name"] == "dynamic_instructions"
        assert descriptor["signature"] == "(ctx) -> str"
        assert descriptor["doc"] == "Resolved per run."
        # ``source`` is the full definition (asserted by substring, not byte-for-byte, so the test does
        # not break on indentation); ``getsource`` includes the ``@agent.instructions`` decorator line.
        assert "def dynamic_instructions(ctx) -> str:" in descriptor["source"]
        assert "@agent.instructions" in descriptor["source"]
        # The primary ``instructions`` string carries the same source inline ("both" shape).
        assert "def dynamic_instructions(ctx) -> str:" in manifest["instructions"]

    def test_manifest_restores_system_prompts_and_omits_dependencies(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: a static ``system_prompt`` is now RESTORED to the manifest (it was dropped by the
        RFC Phase-1 rework), while the removed ``dependencies`` field stays gone. ``deps_type`` lives under
        ``agent_settings`` and MCP under ``capabilities``.
        """
        integration = pydantic_ai._datadog_integration

        class SupportDeps:
            pass

        agent = pydantic_ai.Agent(
            model="gpt-4o", name="test_agent", system_prompt="you are a bot", deps_type=SupportDeps
        )
        manifest = integration._build_agent_manifest(agent)
        # system_prompts is restored (static-only -> a single string, no dynamic flag / functions).
        assert manifest["system_prompts"] == ["you are a bot"]
        assert "system_prompts_is_dynamic" not in manifest
        assert "system_prompt_functions" not in manifest
        # The removed top-level dependencies block stays gone; deps_type surfaces under agent_settings.
        assert "dependencies" not in manifest
        assert manifest["agent_settings"].get("deps_type") == "SupportDeps"

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
        assert manifest["instructions_is_dynamic"] is True
        functions = manifest["instructions_functions"]
        assert len(functions) == 1
        descriptor = functions[0]
        assert descriptor["name"] == "dynamic_instructions"
        assert descriptor["signature"] == "(ctx) -> str"
        assert descriptor["doc"] == "Resolved per run."
        assert "def dynamic_instructions(ctx) -> str:" in descriptor["source"]
        # The "both" shape mirrors the source into the primary instructions string.
        assert "def dynamic_instructions(ctx) -> str:" in manifest["instructions"]

    def test_manifest_captures_static_and_dynamic_system_prompts(self, pydantic_ai, pydantic_ai_llmobs):
        """A static ``system_prompt`` plus a dynamic ``@agent.system_prompt`` function are BOTH captured.

        Builder-driven (runs on every pin -- ``_system_prompts`` / ``_system_prompt_functions`` are stable
        across versions). The static string and the dynamic function's source both land in
        ``system_prompts``; the dynamic function is also described in ``system_prompt_functions`` with the
        ``system_prompts_is_dynamic`` flag set.
        """
        integration = pydantic_ai._datadog_integration
        agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent", system_prompt="You are a bot.")

        @agent.system_prompt
        def dynamic_system_prompt(ctx) -> str:
            """Adds the user name."""
            return "computed system prompt"

        manifest = integration._build_agent_manifest(agent)
        system_prompts = manifest["system_prompts"]
        # Static string is preserved verbatim as the first entry; the dynamic source follows.
        assert len(system_prompts) == 2
        assert system_prompts[0] == "You are a bot."
        assert "def dynamic_system_prompt(ctx) -> str:" in system_prompts[1]
        assert manifest["system_prompts_is_dynamic"] is True
        functions = manifest["system_prompt_functions"]
        assert len(functions) == 1
        descriptor = functions[0]
        assert descriptor["name"] == "dynamic_system_prompt"
        assert descriptor["signature"] == "(ctx) -> str"
        assert descriptor["doc"] == "Adds the user name."
        assert "@agent.system_prompt" in descriptor["source"]

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
        # Static text is preserved and the dynamic source is appended (both in one string).
        assert manifest["instructions"].startswith("You are a helpful assistant.")
        assert "def dynamic_instructions(ctx) -> str:" in manifest["instructions"]
        assert manifest["instructions_is_dynamic"] is True
        # De-duped: the single callable is described exactly once despite any dual storage.
        assert len(manifest["instructions_functions"]) == 1
        assert manifest["instructions_functions"][0]["name"] == "dynamic_instructions"

    def test_manifest_agent_settings_retries_version_tolerant(self, pydantic_ai, pydantic_ai_llmobs):
        """Regression: ``retries`` survives the post-1.63.0 rename of ``_max_result_retries``.

        pydantic-ai <=1.63.0 stores the output-retry budget on ``_max_result_retries``; >1.63.0
        removed that name and stores it on ``_max_output_retries`` (verified on 1.107.0). Runs on
        every pin: construct ``retries=2`` (both names, where present, are 2), then simulate the
        post-rename shape by removing ``_max_result_retries`` while leaving ``_max_output_retries``
        set, and assert the builder still emits ``retries == 2``. Fails against an
        ``_max_result_retries``-only read (the key would silently vanish on >1.63.0). The integration
        never reads ``_max_tool_retries``; a divergent value is set here only as a forward guard, so a
        future refactor that read the tool budget instead of the output budget would be caught.
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
        assert manifest["agent_settings"]["retries"] == 2, manifest["agent_settings"]

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

    async def test_agent_run_with_message_history(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """Test that INPUT_VALUE is set from message_history when user_prompt is not provided."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            result = await agent.run(message_history=message_history)
        spans = [s for trace in test_spans.pop_traces() for s in trace]
        assert len(spans) == 1
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(spans[0]),
            span_kind="agent",
            name="test_agent",
            input_value="Hello from history!",
            output_value=result.output,
            metadata=expected_agent_metadata(),
            tags=PYDANTIC_AI_TAGS,
        )

    async def test_agent_run_stream_with_message_history(
        self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans
    ):
        """Test that INPUT_VALUE is set from message_history for run_stream."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        output = ""
        with request_vcr.use_cassette("agent_run_stream.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
            async with agent.run_stream(message_history=message_history) as result:
                async for chunk in result.stream(debounce_by=None):
                    output = chunk
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

    async def test_agent_iter_with_message_history(self, pydantic_ai, request_vcr, pydantic_ai_llmobs, test_spans):
        """Test that INPUT_VALUE is set from message_history for iter."""
        from pydantic_ai.messages import ModelRequest
        from pydantic_ai.messages import UserPromptPart

        message_history = [ModelRequest(parts=[UserPromptPart(content="Hello from history!")])]
        with request_vcr.use_cassette("agent_iter.yaml"):
            agent = pydantic_ai.Agent(model="gpt-4o", name="test_agent")
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
