import asyncio
from types import SimpleNamespace

import pytest

from ddtrace.contrib.internal.openai_agents.patch import _patched_run_single_turn
from ddtrace.trace import tracer


class TestOpenAIAgentsPatchCompat:
    """Agents-version compatibility shims in patch.py (MLOB-7584).

    agents >= 0.8.0 moved the per-turn fn out of ``AgentRunner`` to module-level ``run_loop``. These
    assert the manifest is captured for the right agent across every call shape; the objects below are
    minimal stand-ins for the real SDK shapes (the SDK only needs to be importable).
    """

    @pytest.fixture(autouse=True)
    def _setup(self, agents, openai_agents_llmobs):
        # ``agents`` (conftest) patches/unpatches; ``openai_agents_llmobs`` enables LLMObs with a mocked
        # writer, setting ``LLMObs.enabled`` (the flag that gates manifest tagging).
        yield

    def _run_module_wrapper(self, monkeypatch, args, kwargs):
        import agents

        integration = agents._datadog_integration
        captured_manifest = []
        captured_agent_side = []
        monkeypatch.setattr(
            integration,
            "_tag_agent_manifest_from_agent",
            lambda span, agent: captured_manifest.append(getattr(agent, "name", None)),
        )
        monkeypatch.setattr(
            integration,
            "record_agent_side",
            lambda trace_id, agent_span_id, **kw: captured_agent_side.append(
                {"trace_id": trace_id, "agent_span_id": agent_span_id, **kw}
            ),
        )

        async def inner(*a, **k):
            return "RESULT"

        async def _run():
            with tracer.trace("test_root"):
                return await _patched_run_single_turn(inner, None, args, kwargs)

        result = asyncio.run(_run())
        return result, captured_manifest, captured_agent_side

    @staticmethod
    def _agent(name="Analyst"):
        return SimpleNamespace(name=name, instructions="x", tools=[{"name": "t"}], handoffs=[], mcp_servers=[])

    def test_patch_wraps_module_level_call_site_targets(self):
        # Behavioral guard: patch() must wrap the call-site re-export ``agents.run.run_single_turn``
        # (not the ``run_loop`` definition — run.py binds the name at import) plus the streamed variant
        # on ``run_loop``. Skipped pre-0.8.0.
        from ddtrace.contrib.internal.openai_agents.patch import _has_module_level_run_loop
        from ddtrace.contrib.trace_utils import iswrapped

        if not _has_module_level_run_loop():
            pytest.skip("instance-method era (< 0.8.0): no module-level run_loop functions")

        import agents.run
        from agents.run_internal import run_loop

        assert iswrapped(agents.run.run_single_turn), "patch() must wrap the agents.run re-export"
        assert iswrapped(run_loop.run_single_turn_streamed), "patch() must wrap run_single_turn_streamed on run_loop"

    # Each test pins one real call shape (verified vs shipped wheels 0.8.0-0.17.x) and asserts the agent
    # the wrapper resolves (via the private ``_tag_agent_manifest_from_agent`` delegate) plus the agent-side
    # context_delta tools_chars the same wrapper records.
    def test_module_wrapper_handles_agent_kwarg_shape(self, monkeypatch):
        # agents 0.8.0-0.13.x non-streamed: run_single_turn(agent=<Agent>, ...). The pre-fix
        # wrapper read only kwargs["bindings"] and silently dropped this.
        agent = self._agent()
        result, manifest, agent_side = self._run_module_wrapper(monkeypatch, (), {"agent": agent, "all_tools": []})
        assert result == "RESULT"
        assert manifest == ["Analyst"]
        # MLOB-7584 — the wrapper also records this agent's context_delta tools_chars, keyed by the agent
        # span_id (the str span_id of the current span the turn ran under).
        assert len(agent_side) == 1
        assert agent_side[0]["tools_chars"] > 0
        assert agent_side[0]["agent_span_id"] and isinstance(agent_side[0]["agent_span_id"], str)

    def test_module_wrapper_handles_bindings_kwarg_shape(self, monkeypatch):
        # agents >= 0.14.0 non-streamed: run_single_turn(bindings=<AgentBindings>, ...). For the
        # agent manifest (the declared config) the public_agent is preferred over execution_agent,
        # which may be a sandbox-rewritten clone.
        public_agent = self._agent("PublicAgent")
        execution_agent = self._agent("ExecutionAgent")
        bindings = SimpleNamespace(public_agent=public_agent, execution_agent=execution_agent)
        result, manifest, agent_side = self._run_module_wrapper(monkeypatch, (), {"bindings": bindings})
        assert result == "RESULT"
        assert manifest == ["PublicAgent"], "must prefer public_agent over execution_agent for the manifest"
        assert len(agent_side) == 1 and agent_side[0]["tools_chars"] > 0

    def test_streamed_wrapper_handles_realistic_positional_bindings(self, monkeypatch):
        # agents >= 0.14.0 streamed: run_single_turn_streamed(<RunResultStreaming>, <bindings>, ...).
        # The real arg[0] is the streamed result (NOT the bindings); the bindings is arg[1].
        agent = self._agent("StreamedExec")
        stream_result = SimpleNamespace(current_agent=agent)  # RunResultStreaming-like: current_agent only
        bindings = SimpleNamespace(public_agent=agent, execution_agent=None)
        result, manifest, agent_side = self._run_module_wrapper(monkeypatch, (stream_result, bindings, "hooks"), {})
        assert result == "RESULT"
        assert manifest == ["StreamedExec"], "must extract agent from bindings at arg[1], not the stream result"
        assert len(agent_side) == 1 and agent_side[0]["tools_chars"] > 0

    def test_streamed_wrapper_handles_realistic_positional_agent(self, monkeypatch):
        # agents 0.8.0-0.13.x streamed: run_single_turn_streamed(<RunResultStreaming>, <Agent>, ...).
        agent = self._agent("StreamedAgent")
        stream_result = SimpleNamespace(current_agent=agent)
        result, manifest, agent_side = self._run_module_wrapper(monkeypatch, (stream_result, agent, "hooks"), {})
        assert result == "RESULT"
        assert manifest == ["StreamedAgent"]
        assert len(agent_side) == 1 and agent_side[0]["tools_chars"] > 0

    def test_module_wrapper_skips_run_result_streaming_without_agent(self, monkeypatch):
        # Negative control: a RunResultStreaming-like object alone (current_agent only, no
        # bindings attrs / no name+tools+handoffs) must NOT be mistaken for an Agent.
        stream_result = SimpleNamespace(current_agent=self._agent())
        result, manifest, agent_side = self._run_module_wrapper(monkeypatch, (stream_result,), {})
        assert result == "RESULT"
        assert manifest == []
        assert agent_side == [], "no agent resolved -> no agent-side context_delta recording"

    def test_wrapper_swallows_capture_errors_so_user_run_survives(self, monkeypatch):
        # A pathological agent whose attribute access raises must NOT propagate out of the wrap
        # site into the user's Runner.run -- the SDK does not guard this call site.
        class _BoomAgent:
            name = "Boom"
            handoffs = []

            @property
            def tools(self):
                raise ValueError("boom")

        result, manifest, agent_side = self._run_module_wrapper(monkeypatch, (), {"agent": _BoomAgent()})
        assert result == "RESULT"  # the user's run completes
        assert manifest == []  # capture degraded gracefully, no raise
        assert agent_side == []

    def test_instance_wrapper_tags_manifest_non_streamed(self, monkeypatch):
        # Regression guard for the instance-method path (AgentRunner._run_single_turn): the
        # agent is passed as the ``agent`` kwarg. This is the path that worked pre-0.8.0 and
        # must keep working. Would fail if _extract_agent_from_call stopped scanning kwargs.
        import agents

        integration = agents._datadog_integration
        captured = []
        monkeypatch.setattr(
            integration,
            "_tag_agent_manifest_from_agent",
            lambda span, agent: captured.append(getattr(agent, "name", None)),
        )

        agent = self._agent("InstanceAgent")

        async def inner(*a, **k):
            return "RESULT"

        async def _run():
            with tracer.trace("test_root"):
                return await _patched_run_single_turn(inner, None, (), {"agent": agent})

        result = asyncio.run(_run())
        assert result == "RESULT"
        assert captured == ["InstanceAgent"]

    def test_instance_wrapper_tags_manifest_streamed_positional(self, monkeypatch):
        # Regression guard for the streamed instance-method path
        # (AgentRunner._run_single_turn_streamed): arg[0] is the streamed result, the agent is
        # arg[1]. Pre-fix instance-method handling read agent_index=1 here; the unified scanner
        # must still resolve arg[1] while skipping the RunResultStreaming at arg[0].
        import agents

        integration = agents._datadog_integration
        captured = []
        monkeypatch.setattr(
            integration,
            "_tag_agent_manifest_from_agent",
            lambda span, agent: captured.append(getattr(agent, "name", None)),
        )

        agent = self._agent("StreamedInstanceAgent")
        stream_result = SimpleNamespace(current_agent=agent)

        async def inner(*a, **k):
            return "RESULT"

        async def _run():
            with tracer.trace("test_root"):
                return await _patched_run_single_turn(inner, None, (stream_result, agent), {})

        result = asyncio.run(_run())
        assert result == "RESULT"
        assert captured == ["StreamedInstanceAgent"]
