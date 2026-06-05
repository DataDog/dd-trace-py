"""Tests for the LLMObs meta_struct + sampling rescue convergence."""

import mock
import pytest

from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import AUTO_REJECT
from ddtrace.constants import USER_KEEP
from ddtrace.constants import USER_REJECT
from ddtrace.ext import SpanTypes
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import CACHED_LLMOBS_EVENT_CTX_KEY
from ddtrace.llmobs._constants import LLMOBS_SUBMITTED_TAG_KEY
from ddtrace.llmobs._constants import LLMObsExportMode
from ddtrace.llmobs._processor import LLMObsProcessor
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.utils import override_global_config


@pytest.fixture
def llmobs_agent_proxy(tracer):
    """LLMObs in APM_AGENT mode with a mock LLMObsSpanWriter so the rescue path
    can be asserted without network I/O.
    """
    llmobs_service.disable()
    with override_global_config(
        {
            "_llmobs_ml_app": "test-ml-app",
            "_dd_api_key": "<not-a-real-key>",
            "service": "tests.llmobs",
        }
    ):
        llmobs_service.enable(_tracer=tracer, agentless_enabled=False, integrations_enabled=False)
        llmobs_service._instance._llmobs_span_writer.stop()
        mock_writer = mock.MagicMock()
        llmobs_service._instance._llmobs_span_writer = mock_writer
        # The processor was bound to the original writer at enable() time; re-bind.
        tracer._span_aggregator.llmobs_processor = LLMObsProcessor(mock_writer, llmobs_service._instance._export_mode)
        yield llmobs_service, mock_writer
        llmobs_service.disable()


def _annotate_llm_span(span, prompt="hello", completion="world"):
    _annotate_llmobs_span_data(
        span,
        kind="llm",
        input_messages=[{"role": "user", "content": prompt}],
        output_messages=[{"role": "assistant", "content": completion}],
    )


def _finish_with_priority(tracer, priority):
    with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
        _annotate_llm_span(span)
        span.context.sampling_priority = priority
    return span


class TestExportModeKeepsMetaStruct:
    def test_apm_agent_proxy_keeps_meta_struct_after_finish(self, llmobs_agent_proxy, tracer):
        _llmobs, mock_writer = llmobs_agent_proxy
        with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
            _annotate_llm_span(span)
            span.context.sampling_priority = USER_KEEP
        assert _get_llmobs_data_metastruct(span)
        mock_writer.enqueue.assert_not_called()
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) is None
        assert span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY) is not None

    def test_apm_agentless_keeps_meta_struct_and_skips_rescue(self, tracer):
        """APM_AGENTLESS ships straight to intake at 100%, so finish must keep meta_struct,
        cache no rescue event, and never enqueue to the writer — even on a predicted drop.
        """
        from ddtrace._trace.processor import _NoopTraceProcessor

        llmobs_service.disable()
        with override_global_config(
            {
                "_llmobs_ml_app": "test-ml-app",
                "_dd_api_key": "<not-a-real-key>",
                "service": "tests.llmobs",
            }
        ):
            llmobs_service.enable(_tracer=tracer, agentless_enabled=False, integrations_enabled=False)
            llmobs_service._instance._export_mode = LLMObsExportMode.APM_AGENTLESS
            # Mirror enable() gating: agentless installs no rescue processor.
            tracer._span_aggregator.llmobs_processor = _NoopTraceProcessor()
            llmobs_service._instance._llmobs_span_writer.stop()
            mock_writer = mock.MagicMock()
            llmobs_service._instance._llmobs_span_writer = mock_writer
            with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
                _annotate_llm_span(span)
                span.context.sampling_priority = USER_REJECT
            mock_writer.enqueue.assert_not_called()
            assert _get_llmobs_data_metastruct(span)
            assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) is None
            assert span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY) is None
            llmobs_service.disable()

    def test_llmobs_direct_mode_still_enqueues_and_scrubs(self, tracer):
        llmobs_service.disable()
        with override_global_config(
            {
                "_llmobs_ml_app": "test-ml-app",
                "_dd_api_key": "<not-a-real-key>",
                "service": "tests.llmobs",
            }
        ):
            llmobs_service.enable(_tracer=tracer, agentless_enabled=False, integrations_enabled=False)
            llmobs_service._instance._export_mode = LLMObsExportMode.LLMOBS_DIRECT
            llmobs_service._instance._llmobs_span_writer.stop()
            mock_writer = mock.MagicMock()
            llmobs_service._instance._llmobs_span_writer = mock_writer
            with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
                _annotate_llm_span(span)
            mock_writer.enqueue.assert_called_once()
            assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"
            assert not _get_llmobs_data_metastruct(span)
            llmobs_service.disable()


class TestRescuePath:
    def test_rescue_fires_on_user_reject(self, llmobs_agent_proxy, tracer):
        _llmobs, mock_writer = llmobs_agent_proxy
        span = _finish_with_priority(tracer, USER_REJECT)
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"
        assert not _get_llmobs_data_metastruct(span)
        mock_writer.enqueue.assert_called_once()

    def test_rescue_fires_on_auto_reject(self, llmobs_agent_proxy, tracer):
        _llmobs, mock_writer = llmobs_agent_proxy
        span = _finish_with_priority(tracer, AUTO_REJECT)
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"
        assert not _get_llmobs_data_metastruct(span)
        mock_writer.enqueue.assert_called_once()

    def test_rescue_skipped_for_kept_priority(self, llmobs_agent_proxy, tracer):
        _llmobs, mock_writer = llmobs_agent_proxy
        span = _finish_with_priority(tracer, USER_KEEP)
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) is None
        assert _get_llmobs_data_metastruct(span)
        mock_writer.enqueue.assert_not_called()

    def test_rescue_idempotent_when_already_submitted(self, llmobs_agent_proxy, tracer):
        """The submitted tag marks an event that was already shipped to the writer (e.g. via
        the finish-time direct path). On a predicted drop the rescue must honor that flag and
        not enqueue a duplicate.
        """
        _llmobs, mock_writer = llmobs_agent_proxy
        with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
            _annotate_llm_span(span)
            # Simulate "already submitted" before the rescue processor runs.
            span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
            span.context.sampling_priority = USER_REJECT
        mock_writer.enqueue.assert_not_called()
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"

    def test_rescue_does_not_mutate_sampling_priority(self, llmobs_agent_proxy, tracer):
        """LLMObs has zero impact on APM sampling decisions or billing."""
        _llmobs, _ = llmobs_agent_proxy
        with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
            _annotate_llm_span(span)
            span.context.sampling_priority = USER_REJECT
        assert span.context.sampling_priority == USER_REJECT
        assert span.get_metric("_dd.span_sampling.mechanism") is None
        assert span.get_metric("_dd.span_sampling.rule_rate") is None

    def test_rescue_ignores_non_llm_spans(self, llmobs_agent_proxy, tracer):
        _llmobs, mock_writer = llmobs_agent_proxy
        with tracer.trace("apm-span") as span:
            span.context.sampling_priority = USER_REJECT
        mock_writer.enqueue.assert_not_called()
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) is None

    def test_rescue_uses_local_root_priority(self, llmobs_agent_proxy, tracer):
        """Distributed-trace case: rescue reads the local root's priority, not the
        child's, so an upstream reject still triggers.
        """
        _llmobs, mock_writer = llmobs_agent_proxy
        with tracer.trace("root-apm-span") as root:
            root.context.sampling_priority = USER_REJECT
            with tracer.trace("llm-child", span_type=SpanTypes.LLM) as child:
                _annotate_llm_span(child)
        assert child.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"
        mock_writer.enqueue.assert_called_once()


class TestV04Forcing:
    def test_enable_forces_v04(self, tracer):
        """LLMObs.enable() must recreate the APM writer at v0.4 even when the user
        configured DD_TRACE_API_VERSION=v0.5 (v0.5 strips meta_struct).
        """
        from ddtrace.internal.writer import NativeWriter

        original = NativeWriter("http://dne:1234", api_version="v0.5")
        assert original._api_version == "v0.5"

        llmobs_service.disable()
        with override_global_config(
            {
                "_llmobs_ml_app": "test-ml-app",
                "_dd_api_key": "<not-a-real-key>",
                "service": "tests.llmobs",
            }
        ):
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


class TestProcessorChainOrdering:
    def test_chain_order_includes_llmobs_slot(self, tracer):
        assert tracer._span_aggregator.llmobs_processor is not None

    def test_default_slot_is_noop(self, tracer):
        from ddtrace._trace.processor import _NoopTraceProcessor

        assert isinstance(tracer._span_aggregator.llmobs_processor, _NoopTraceProcessor)

    def test_enable_disable_swaps_slot(self, tracer):
        from ddtrace._trace.processor import _NoopTraceProcessor

        llmobs_service.disable()
        with override_global_config(
            {
                "_llmobs_ml_app": "test-ml-app",
                "_dd_api_key": "<not-a-real-key>",
                "service": "tests.llmobs",
            }
        ):
            llmobs_service.enable(_tracer=tracer, agentless_enabled=False, integrations_enabled=False)
            assert isinstance(tracer._span_aggregator.llmobs_processor, LLMObsProcessor)
            llmobs_service.disable()
            assert isinstance(tracer._span_aggregator.llmobs_processor, _NoopTraceProcessor)

    def test_chain_positions_rescue_between_sampling_and_tags(self, tracer):
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
        rescue_pos = next(i for i, p in enumerate(chain_order) if p is agg.llmobs_processor)
        assert positions["TraceSamplingProcessor"] < rescue_pos
        assert rescue_pos < positions["TraceTagsProcessor"]
        assert positions["TraceTagsProcessor"] < positions["ServiceNameProcessor"]


class TestRescueEdgeCases:
    def test_predicted_drop_without_cached_event_scrubs_meta_struct(self):
        """No cached event (build raised mid-annotation) + predicted-drop must still
        scrub meta_struct so a half-built payload never ships.
        """
        from ddtrace._trace.span import Span
        from ddtrace.llmobs._constants import LLMOBS_STRUCT

        mock_writer = mock.MagicMock()
        processor = LLMObsProcessor(mock_writer, LLMObsExportMode.APM_AGENT)

        span = Span(name="llm-span", span_type=SpanTypes.LLM)
        span._set_struct_tag(LLMOBS_STRUCT.KEY, {"trace_id": "abc", "span_id": "1"})
        span.context.sampling_priority = USER_REJECT
        # No _set_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY, ...) — the missing cache is the
        # whole point of this test.
        assert span._get_struct_tag(LLMOBS_STRUCT.KEY) is not None

        result = processor.process_trace([span])

        assert result == [span]
        mock_writer.enqueue.assert_not_called()
        assert span._get_struct_tag(LLMOBS_STRUCT.KEY) is None
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) is None

    def test_rescue_returns_trace_unchanged(self):
        """APM trace identity / order is invariant under the rescue processor."""
        from ddtrace._trace.span import Span

        mock_writer = mock.MagicMock()
        processor = LLMObsProcessor(mock_writer, LLMObsExportMode.APM_AGENT)
        root = Span(name="root-apm-span")
        child_llm = Span(name="llm-child", span_type=SpanTypes.LLM)
        unrelated = Span(name="apm-child")
        root.context.sampling_priority = USER_REJECT
        child_llm.context = root.context
        unrelated.context = root.context

        trace = [root, child_llm, unrelated]
        result = processor.process_trace(trace)

        assert result is trace
        assert [s.name for s in result] == ["root-apm-span", "llm-child", "apm-child"]


class TestTracerDisabledImmediateShip:
    def test_tracer_disabled_enqueues_at_finish_hook(self, tracer):
        """tracer.enabled=False short-circuits SpanAggregator.on_span_finish, so the
        rescue chain never runs. _on_span_finish must enqueue inline instead.
        """
        llmobs_service.disable()
        with override_global_config(
            {
                "_llmobs_ml_app": "test-ml-app",
                "_dd_api_key": "<not-a-real-key>",
                "service": "tests.llmobs",
            }
        ):
            llmobs_service.enable(_tracer=tracer, agentless_enabled=False, integrations_enabled=False)
            assert llmobs_service._instance._export_mode == LLMObsExportMode.APM_AGENT
            llmobs_service._instance._llmobs_span_writer.stop()
            mock_writer = mock.MagicMock()
            llmobs_service._instance._llmobs_span_writer = mock_writer
            tracer.enabled = False
            try:
                with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
                    _annotate_llm_span(span)
                mock_writer.enqueue.assert_called_once()
                assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"
                assert not _get_llmobs_data_metastruct(span)
            finally:
                tracer.enabled = True
                llmobs_service.disable()


class TestSamplingPriorityKeyPresent:
    def test_priority_round_trips(self, tracer):
        with tracer.trace("apm-span") as span:
            span.context.sampling_priority = USER_REJECT
            assert span.context._metrics[_SAMPLING_PRIORITY_KEY] == USER_REJECT
