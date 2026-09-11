"""Cross-product tests: a security/AI product in standalone mode alongside LLM Observability.

Standalone mode (DD_APM_TRACING_ENABLED=false plus an enabled product) turns APM tracing off and
disables the tracer on purpose, while still requiring the product's own traces to reach the
backend. LLMObs owns a trace processor that drops every APM trace when APM tracing is off, so the
two features have to be exercised together: each product's own suite runs with a no-op LLMObs
processor and cannot catch the interaction.
"""

from unittest.mock import MagicMock
from unittest.mock import patch

import pytest

import ddtrace
from ddtrace.aiguard._constants import AI_GUARD
from ddtrace.constants import USER_KEEP
from ddtrace.ext import SpanTypes
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.constants import SamplingMechanism
from ddtrace.internal.settings.standalone import standalone_config
from ddtrace.llmobs._processor import LLMObsProcessor
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config
from tests.utils import DummyWriter


MESSAGES = [{"role": "user", "content": "What is the meaning of life?"}]

_STANDALONE_AI_GUARD_CONFIG = dict(
    _ai_guard_enabled="True",
    _ai_guard_endpoint="https://api.example.com/ai-guard",
    _dd_api_key="test-api-key",
    _dd_app_key="test-app-key",
)


def _install_llmobs_processor(tracer):
    """Attach a real LLMObsProcessor plus a fresh writer, returning the writer.

    Both are installed after the caller's tracer.configure(), which recreates the writer via
    _recreate and would otherwise discard it.
    """
    writer = DummyWriter(trace_flush_enabled=False)
    tracer._span_aggregator.writer = writer
    tracer._span_aggregator.llmobs_processor = LLMObsProcessor(MagicMock(), tracer)
    return writer


@pytest.fixture
def ai_guard_standalone_tracer(tracer):
    """AI Guard enabled with APM tracing disabled, restored afterwards."""
    with override_ai_guard_config(_STANDALONE_AI_GUARD_CONFIG):
        # compute_stats_enabled forces tracer._recreate so the sampling processor picks up
        # apm_opt_out, which is now True (AI Guard enabled + APM tracing disabled).
        tracer.configure(apm_tracing_disabled=True, compute_stats_enabled=False)
        original_processor = tracer._span_aggregator.llmobs_processor
        try:
            yield tracer
        finally:
            tracer._span_aggregator.llmobs_processor = original_processor
            tracer.configure(apm_tracing_disabled=False, compute_stats_enabled=False)
            ddtrace.config._reset()


@pytest.fixture
def appsec_standalone_tracer(tracer):
    """AppSec enabled with APM tracing disabled, restored afterwards."""
    tracer.configure(appsec_enabled=True, apm_tracing_disabled=True, compute_stats_enabled=False)
    original_processor = tracer._span_aggregator.llmobs_processor
    try:
        yield tracer
    finally:
        tracer._span_aggregator.llmobs_processor = original_processor
        tracer.configure(appsec_enabled=False, apm_tracing_disabled=False, compute_stats_enabled=False)
        ddtrace.config._reset()


class TestAIGuardStandaloneWithLLMObs:
    @patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
    def test_ai_guard_trace_survives_llmobs_processor(self, mock_execute_request, ai_guard_standalone_tracer):
        """Enabling LLM Observability must not lose AI Guard's spans."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        tracer = ai_guard_standalone_tracer
        assert standalone_config.apm_opt_out is True
        writer = _install_llmobs_processor(tracer)

        from ddtrace.aiguard import new_ai_guard_client

        client = new_ai_guard_client()
        with tracer.trace("root_span", span_type=SpanTypes.WEB):
            client.evaluate(MESSAGES)

        written = [span.name for trace in writer.pop_traces() for span in trace]
        assert AI_GUARD.RESOURCE_TYPE in written, "LLMObs processor dropped the standalone AI Guard trace"

    @patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
    def test_standalone_contract_intact_with_llmobs_processor(self, mock_execute_request, ai_guard_standalone_tracer):
        """The whole standalone contract must hold with LLM Observability on: the trace is kept
        with the AI_GUARD decision maker, APM billing stays opted out, and the ai_guard span still
        carries the evaluation payload the backend reads.
        """
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        tracer = ai_guard_standalone_tracer
        writer = _install_llmobs_processor(tracer)

        from ddtrace.aiguard import new_ai_guard_client

        client = new_ai_guard_client()
        with tracer.trace("root_span", span_type=SpanTypes.WEB) as root_span:
            client.evaluate(MESSAGES)

        assert root_span.context.sampling_priority == USER_KEEP
        assert root_span.get_tag(SAMPLING_DECISION_TRACE_TAG_KEY) == "-%d" % SamplingMechanism.AI_GUARD
        assert root_span.get_metric("_dd.apm.enabled") == 0.0

        spans = [span for trace in writer.pop_traces() for span in trace]
        ai_guard_span = next((span for span in spans if span.name == AI_GUARD.RESOURCE_TYPE), None)
        assert ai_guard_span is not None, "LLMObs processor dropped the standalone AI Guard trace"
        assert ai_guard_span.get_tag(AI_GUARD.ACTION_TAG) == "ALLOW"
        # The evaluation payload rides the span, so losing it silently empties AI Guard events.
        assert ai_guard_span._get_struct_tag(AI_GUARD.STRUCT) is not None


class TestAppSecStandaloneWithLLMObs:
    def test_appsec_trace_survives_llmobs_processor(self, appsec_standalone_tracer):
        """apm_opt_out is shared, so the same drop hits AppSec standalone, not just AI Guard."""
        tracer = appsec_standalone_tracer
        assert standalone_config.apm_opt_out is True
        writer = _install_llmobs_processor(tracer)

        with tracer.trace("appsec_root", span_type=SpanTypes.WEB) as span:
            pass

        written = [span.name for trace in writer.pop_traces() for span in trace]
        assert "appsec_root" in written, "LLMObs processor dropped the standalone AppSec trace"
        assert span.get_metric("_dd.apm.enabled") == 0.0
