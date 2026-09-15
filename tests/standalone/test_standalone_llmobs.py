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
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._constants import LLMOBS_SUBMITTED_TAG_KEY
from ddtrace.llmobs._constants import LLMObsExportMode
from ddtrace.llmobs._processor import LLMObsProcessor
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.aiguard.utils import mock_evaluate_response
from tests.aiguard.utils import override_ai_guard_config
from tests.utils import DummyWriter
from tests.utils import override_global_config


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
        tracer.configure(apm_tracing_disabled=True)
        original_processor = tracer._span_aggregator.llmobs_processor
        try:
            yield tracer
        finally:
            tracer._span_aggregator.llmobs_processor = original_processor
            tracer.configure(apm_tracing_disabled=False)
            ddtrace.config._reset()


@pytest.fixture
def appsec_standalone_tracer(tracer):
    """AppSec enabled with APM tracing disabled, restored afterwards."""
    tracer.configure(appsec_enabled=True, apm_tracing_disabled=True)
    original_processor = tracer._span_aggregator.llmobs_processor
    try:
        yield tracer
    finally:
        tracer._span_aggregator.llmobs_processor = original_processor
        tracer.configure(appsec_enabled=False, apm_tracing_disabled=False)
        ddtrace.config._reset()


class TestAIGuardStandaloneWithLLMObs:
    @patch("ddtrace.aiguard._api_client.AIGuardClient._execute_request")
    def test_ai_guard_trace_survives_llmobs_processor(self, mock_execute_request, ai_guard_standalone_tracer):
        """Enabling LLM Observability must not lose AI Guard's spans."""
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        tracer = ai_guard_standalone_tracer
        assert asm_config._apm_opt_out is True
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
        assert asm_config._apm_opt_out is True
        writer = _install_llmobs_processor(tracer)

        with tracer.trace("appsec_root", span_type=SpanTypes.WEB) as span:
            pass

        written = [span.name for trace in writer.pop_traces() for span in trace]
        assert "appsec_root" in written, "LLMObs processor dropped the standalone AppSec trace"
        assert span.get_metric("_dd.apm.enabled") == 0.0


class TestRuntimeSwitchIntoStandalone:
    """tracer.configure(apm_tracing_disabled=True) is a supported runtime switch, and it has to
    refresh the sampling processor: otherwise the trace is delivered without the standalone rate
    limit or _dd.apm.enabled=0, and is billed as ordinary APM.
    """

    def test_sampling_processor_picks_up_the_opt_out(self, ai_guard_standalone_tracer):
        tracer = ai_guard_standalone_tracer
        sampling_processor = tracer._span_aggregator.sampling_processor

        assert asm_config._apm_opt_out is True
        assert sampling_processor.apm_opt_out is True, "sampling processor kept a stale apm_opt_out"
        # The opt-out limiter keeps the service visible at 1 trace per minute.
        assert sampling_processor.sampler._rate_limit_always_on is True

    def test_delivered_trace_is_still_opted_out_of_apm_billing(self, ai_guard_standalone_tracer):
        """The pairing that matters: the trace survives the LLMObs processor *and* carries the
        opt-out metric. Delivering it without the metric would start billing it as APM.
        """
        tracer = ai_guard_standalone_tracer
        writer = _install_llmobs_processor(tracer)

        with tracer.trace("root_span", span_type=SpanTypes.WEB) as span:
            pass

        written = [s.name for trace in writer.pop_traces() for s in trace]
        assert "root_span" in written
        assert span.get_metric("_dd.apm.enabled") == 0.0


@pytest.fixture
def llmobs_enabled_before_the_switch(tracer):
    """LLM Observability enabled while APM tracing is still on, then switched into standalone.

    This is the order a real application hits: LLMObs starts at import time and the product only
    turns APM tracing off afterwards, through the public tracer.configure() API.
    """
    LLMObs.disable()
    with override_global_config({"_llmobs_ml_app": "test-ml-app", "_dd_api_key": "<not-a-real-key>"}):
        LLMObs.enable(_tracer=tracer, agentless_enabled=False, integrations_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        llmobs_writer = MagicMock()
        LLMObs._instance._llmobs_span_writer = llmobs_writer
        with override_ai_guard_config(_STANDALONE_AI_GUARD_CONFIG):
            tracer.configure(apm_tracing_disabled=True)
            tracer._span_aggregator.llmobs_processor = LLMObsProcessor(llmobs_writer, tracer)
            try:
                yield llmobs_writer
            finally:
                tracer.configure(apm_tracing_disabled=False)
                ddtrace.config._reset()
        LLMObs.disable()


class TestLLMObsEnabledBeforeTheSwitch:
    """LLMObs resolves its export mode once, in LLMObs.__init__, and the switch into standalone
    never refreshes it. Keeping the standalone trace must not reroute the event onto it: standalone
    traces are rate limited to 1/minute and opted out of APM, so the event would be lost.
    """

    def test_llmobs_event_still_reaches_its_own_intake(self, llmobs_enabled_before_the_switch, tracer):
        llmobs_writer = llmobs_enabled_before_the_switch
        assert asm_config._apm_opt_out is True
        # Stale on purpose: nothing refreshes it, which is the whole point of this test.
        assert LLMObs._instance._export_mode == LLMObsExportMode.APM_AGENT
        writer = DummyWriter(trace_flush_enabled=False)
        tracer._span_aggregator.writer = writer

        with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
            _annotate_llmobs_span_data(
                span,
                kind="llm",
                input_messages=[{"role": "user", "content": "What is the meaning of life?"}],
                output_messages=[{"role": "assistant", "content": "42"}],
            )

        assert "llm-span" in [s.name for trace in writer.pop_traces() for s in trace]
        assert span.get_metric("_dd.apm.enabled") == 0.0
        llmobs_writer.enqueue.assert_called_once()
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"
        assert not _get_llmobs_data_metastruct(span)
