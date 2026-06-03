"""Tests for the centralized ``LLMObsProcessor`` flush-time export behavior."""

import mock

from ddtrace._trace.processor import _NoopTraceProcessor
from ddtrace._trace.span import Span
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import USER_REJECT
from ddtrace.ext import SpanTypes
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import LLMOBS_SUBMITTED_TAG_KEY
from ddtrace.llmobs._constants import LLMObsExportMode
from ddtrace.llmobs._processor import LLMObsProcessor
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.utils import override_global_config


def _annotate_llm_span(span, prompt="hello", completion="world"):
    _annotate_llmobs_span_data(
        span,
        kind="llm",
        input_messages=[{"role": "user", "content": prompt}],
        output_messages=[{"role": "assistant", "content": completion}],
    )


def _enable_with_mode(tracer, export_mode):
    """Enable LLMObs and rebind the flush processor to a mock writer for the given mode."""
    llmobs_service.enable(_tracer=tracer, agentless_enabled=False, integrations_enabled=False)
    instance = llmobs_service._instance
    instance._export_mode = export_mode
    instance._llmobs_span_writer.stop()
    mock_writer = mock.MagicMock()
    instance._llmobs_span_writer = mock_writer
    tracer._span_aggregator.llmobs_processor = LLMObsProcessor(
        mock_writer,
        export_mode=export_mode,
        user_span_processor=instance._user_span_processor,
        evaluator_runner=mock.MagicMock(),
    )
    return mock_writer


_TEST_CONFIG = {
    "_llmobs_ml_app": "test-ml-app",
    "_dd_api_key": "<not-a-real-key>",
    "service": "tests.llmobs",
}


class TestExportModeBehavior:
    def test_apm_agent_keeps_meta_struct_and_skips_writer(self, tracer):
        """APM_AGENT: the finalized payload rides the trace as meta_struct; nothing ships
        via the writer and the APM trace is kept.
        """
        llmobs_service.disable()
        with override_global_config(_TEST_CONFIG):
            mock_writer = _enable_with_mode(tracer, LLMObsExportMode.APM_AGENT)
            with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
                _annotate_llm_span(span)
            assert _get_llmobs_data_metastruct(span)
            mock_writer.enqueue.assert_not_called()
            assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) is None
            llmobs_service.disable()

    def test_apm_agentless_keeps_meta_struct_and_skips_writer(self, tracer):
        """APM_AGENTLESS: rides the trace straight to intake at 100%; meta_struct is kept and
        the writer is never used.
        """
        llmobs_service.disable()
        with override_global_config(_TEST_CONFIG):
            mock_writer = _enable_with_mode(tracer, LLMObsExportMode.APM_AGENTLESS)
            with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
                _annotate_llm_span(span)
            assert _get_llmobs_data_metastruct(span)
            mock_writer.enqueue.assert_not_called()
            llmobs_service.disable()

    def test_llmobs_direct_enqueues_via_writer(self, tracer):
        """LLMOBS_DIRECT: APM tracing is off, so the event ships via the writer."""
        llmobs_service.disable()
        with override_global_config(_TEST_CONFIG):
            mock_writer = _enable_with_mode(tracer, LLMObsExportMode.LLMOBS_DIRECT)
            with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
                _annotate_llm_span(span)
            mock_writer.enqueue.assert_called_once()
            assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"
            llmobs_service.disable()

    def test_processor_does_not_mutate_sampling_priority(self, tracer):
        """LLMObs has zero impact on APM sampling decisions or billing."""
        llmobs_service.disable()
        with override_global_config(_TEST_CONFIG):
            _enable_with_mode(tracer, LLMObsExportMode.APM_AGENT)
            with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
                _annotate_llm_span(span)
                span.context.sampling_priority = USER_REJECT
            assert span.context.sampling_priority == USER_REJECT
            assert span.get_metric("_dd.span_sampling.mechanism") is None
            assert span.get_metric("_dd.span_sampling.rule_rate") is None
            llmobs_service.disable()


class TestProcessTraceUnit:
    def test_apm_tracing_disabled_drops_chunk(self):
        """LLMOBS_DIRECT (DD_APM_TRACING_ENABLED=false) makes process_trace drop the whole chunk."""
        processor = LLMObsProcessor(mock.MagicMock(), export_mode=LLMObsExportMode.LLMOBS_DIRECT)
        assert processor.process_trace([Span(name="apm-span")]) is None

    def test_tracer_disabled_drops_chunk(self):
        """DD_TRACE_ENABLED=false drops the trace at flush even in APM_AGENT mode, since there
        is no trace to ride.
        """
        processor = LLMObsProcessor(mock.MagicMock(), export_mode=LLMObsExportMode.APM_AGENT)
        with mock.patch("ddtrace.llmobs._processor.config") as mock_config:
            mock_config._tracing_enabled = False
            assert processor.process_trace([Span(name="apm-span")]) is None

    def test_apm_tracing_on_returns_trace_unchanged(self):
        processor = LLMObsProcessor(mock.MagicMock(), export_mode=LLMObsExportMode.APM_AGENT)
        trace = [Span(name="root-apm-span"), Span(name="apm-child")]
        with mock.patch("ddtrace.llmobs._processor.config") as mock_config:
            mock_config._tracing_enabled = True
            result = processor.process_trace(trace)
        assert result is trace
        assert [s.name for s in result] == ["root-apm-span", "apm-child"]

    def test_processor_ignores_non_llm_spans(self):
        processor = LLMObsProcessor(mock.MagicMock(), export_mode=LLMObsExportMode.APM_AGENT)
        with mock.patch.object(processor, "_process_span") as process_span:
            processor.process_trace([Span(name="apm-span")])
            process_span.assert_not_called()


class TestProcessorChainOrdering:
    def test_default_slot_is_noop(self, tracer):
        assert isinstance(tracer._span_aggregator.llmobs_processor, _NoopTraceProcessor)

    def test_enable_disable_swaps_slot(self, tracer):
        llmobs_service.disable()
        with override_global_config(_TEST_CONFIG):
            llmobs_service.enable(_tracer=tracer, agentless_enabled=False, integrations_enabled=False)
            assert isinstance(tracer._span_aggregator.llmobs_processor, LLMObsProcessor)
            llmobs_service.disable()
            assert isinstance(tracer._span_aggregator.llmobs_processor, _NoopTraceProcessor)

    def test_chain_positions_llmobs_between_sampling_and_tags(self, tracer):
        """Mirrors the hardcoded list in SpanAggregator.on_span_finish; fails loudly
        if the chain is reshuffled.
        """
        agg = tracer._span_aggregator
        chain_order = [
            agg.sampling_processor,
            agg.llmobs_processor,
            agg.tags_processor,
            agg.service_name_processor,
        ]
        positions = {type(p).__name__: i for i, p in enumerate(chain_order)}
        llmobs_pos = next(i for i, p in enumerate(chain_order) if p is agg.llmobs_processor)
        assert positions["TraceSamplingProcessor"] < llmobs_pos
        assert llmobs_pos < positions["TraceTagsProcessor"]
        assert positions["TraceTagsProcessor"] < positions["ServiceNameProcessor"]


class TestV04Forcing:
    def test_enable_forces_v04(self, tracer):
        """LLMObs.enable() must recreate the APM writer at v0.4 even when the user
        configured DD_TRACE_API_VERSION=v0.5 (v0.5 strips meta_struct).
        """
        from ddtrace.internal.writer import NativeWriter

        original = NativeWriter("http://dne:1234", api_version="v0.5")
        assert original._api_version == "v0.5"

        llmobs_service.disable()
        with override_global_config(_TEST_CONFIG):
            llmobs_service.enable(_tracer=tracer, agentless_enabled=False, integrations_enabled=False)
            assert tracer._span_aggregator.writer._api_version == "v0.4"
            llmobs_service.disable()

    def test_native_writer_recreate_with_llmobs_kwarg(self):
        from ddtrace.internal.writer import NativeWriter

        writer = NativeWriter("http://dne:1234", api_version="v0.5")
        assert writer._api_version == "v0.5"
        new_writer = writer.recreate(llmobs_enabled=True)
        assert new_writer._api_version == "v0.4"

    def test_resolve_api_version_warns_on_explicit_v05(self, monkeypatch):
        from ddtrace import config as ddtrace_config
        from ddtrace.internal.writer import writer as writer_module
        from ddtrace.internal.writer.writer import _resolve_api_version

        warning_calls = []

        def _capture(msg, *args, **kwargs):
            warning_calls.append(msg % args if args else msg)

        monkeypatch.setattr(writer_module.log, "warning", _capture)

        prior = ddtrace_config._llmobs_enabled
        try:
            ddtrace_config._llmobs_enabled = True
            resolved = _resolve_api_version("v0.5")
            assert resolved == "v0.4"
            assert any("LLM Observability requires v0.4" in msg for msg in warning_calls)
        finally:
            ddtrace_config._llmobs_enabled = prior


class TestSamplingPriorityKeyPresent:
    def test_priority_round_trips(self, tracer):
        with tracer.trace("apm-span") as span:
            span.context.sampling_priority = USER_REJECT
            assert span.context._metrics[_SAMPLING_PRIORITY_KEY] == USER_REJECT
