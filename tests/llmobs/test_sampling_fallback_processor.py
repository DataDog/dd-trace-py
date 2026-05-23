"""Tests for the LLMObs meta_struct + sampling rescue convergence (Phases A/B/C of the
agent-based LLMObs meta_struct rollout).
"""

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
from ddtrace.llmobs._sampling_fallback_processor import LLMObsSamplingFallbackProcessor
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from tests.utils import override_global_config


@pytest.fixture
def llmobs_agent_proxy(tracer):
    """LLMObs configured for the APM_AGENT_PROXY default: meta_struct rides the APM trace,
    rescue fires only on predicted-drop.
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
        # Replace the real writer with a mock so we never hit the network and so we can
        # assert on enqueue behavior during the rescue path.
        llmobs_service._instance._llmobs_span_writer.stop()
        mock_writer = mock.MagicMock()
        llmobs_service._instance._llmobs_span_writer = mock_writer
        # Rewire the processor instance's writer reference too (the LLMObsSamplingFallbackProcessor
        # captured the original at enable time).
        tracer._span_aggregator.llmobs_fallback_processor = LLMObsSamplingFallbackProcessor(mock_writer)
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
    """Open and finish an LLM span with the given root sampling priority."""
    with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
        _annotate_llm_span(span)
        span.context.sampling_priority = priority
    return span


class TestExportModeKeepsMetaStruct:
    """Phase B: APM_AGENT_PROXY / APM_AGENTLESS keep meta_struct on the span at the
    LLMObs._on_span_finish hook.
    """

    def test_apm_agent_proxy_keeps_meta_struct_after_finish(self, llmobs_agent_proxy, tracer):
        """meta_struct["_llmobs"] is still present on the span after the LLMObs finish hook
        in APM_AGENT_PROXY mode, and no event is enqueued at the hook.
        """
        _llmobs, mock_writer = llmobs_agent_proxy
        with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
            _annotate_llm_span(span)
            span.context.sampling_priority = USER_KEEP
        # Finish hook ran; meta_struct stays so the APM trace can carry the payload.
        assert _get_llmobs_data_metastruct(span)
        # Writer is not invoked at the hook for APM_AGENT_PROXY kept traces.
        mock_writer.enqueue.assert_not_called()
        # The submitted tag is NOT set on the kept path (rescue did not fire).
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) is None
        # The cached event is stashed for the rescue processor to consume.
        assert span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY) is not None

    def test_llmobs_direct_mode_still_enqueues_and_scrubs(self, tracer):
        """LLMOBS_DIRECT (DD_APM_TRACING_ENABLED=false) keeps the immediate-ship behavior
        because the APM trace is dropped by APMTracingEnabledFilter.
        """
        llmobs_service.disable()
        with override_global_config(
            {
                "_llmobs_ml_app": "test-ml-app",
                "_dd_api_key": "<not-a-real-key>",
                "service": "tests.llmobs",
            }
        ):
            # Force the direct-write mode regardless of env to keep the test hermetic.
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
    """Phase C: LLMObsSamplingFallbackProcessor fires when the SDK predicts a drop."""

    def test_rescue_fires_on_user_reject(self, llmobs_agent_proxy, tracer):
        """When the root sampling priority is USER_REJECT, the rescue path re-ships the
        cached LLMObs event via the writer and scrubs meta_struct.
        """
        _llmobs, mock_writer = llmobs_agent_proxy
        span = _finish_with_priority(tracer, USER_REJECT)
        # Wait for the processor chain to run; the finish + chain run on the same thread.
        # Assert the rescue side-effects landed on the span.
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
        """Kept traces let meta_struct ride the APM trace and never invoke the writer."""
        _llmobs, mock_writer = llmobs_agent_proxy
        span = _finish_with_priority(tracer, USER_KEEP)
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) is None
        assert _get_llmobs_data_metastruct(span)
        mock_writer.enqueue.assert_not_called()

    def test_rescue_idempotent_when_already_submitted(self, llmobs_agent_proxy, tracer):
        """If _dd.llmobs.submitted=1 is already set (e.g. by a re-flush) the rescue does
        not fire a second time, preventing duplicates.
        """
        _llmobs, mock_writer = llmobs_agent_proxy
        with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
            _annotate_llm_span(span)
            span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
            span.context.sampling_priority = USER_REJECT
        mock_writer.enqueue.assert_not_called()
        # Pre-existing submitted tag preserved.
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"

    def test_rescue_does_not_mutate_sampling_priority(self, llmobs_agent_proxy, tracer):
        """LLMObs has zero impact on APM sampling decisions or billing."""
        _llmobs, _ = llmobs_agent_proxy
        with tracer.trace("llm-span", span_type=SpanTypes.LLM) as span:
            _annotate_llm_span(span)
            span.context.sampling_priority = USER_REJECT
        # Priority is unchanged after the chain ran.
        assert span.context.sampling_priority == USER_REJECT
        # No single-span sampling markers were added.
        assert span.get_metric("_dd.span_sampling.mechanism") is None
        assert span.get_metric("_dd.span_sampling.rule_rate") is None

    def test_rescue_ignores_non_llm_spans(self, llmobs_agent_proxy, tracer):
        """Non-LLM spans in the same trace are never enqueued and never touched."""
        _llmobs, mock_writer = llmobs_agent_proxy
        with tracer.trace("apm-span") as span:
            span.context.sampling_priority = USER_REJECT
        mock_writer.enqueue.assert_not_called()
        assert span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) is None

    def test_rescue_uses_local_root_priority(self, llmobs_agent_proxy, tracer):
        """Per-span sampling decisions in distributed traces — the rescue reads the local
        root's priority, not the child's.
        """
        _llmobs, mock_writer = llmobs_agent_proxy
        with tracer.trace("root-apm-span") as root:
            root.context.sampling_priority = USER_REJECT
            with tracer.trace("llm-child", span_type=SpanTypes.LLM) as child:
                _annotate_llm_span(child)
        # The child rescued based on the root's priority.
        assert child.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"
        mock_writer.enqueue.assert_called_once()


class TestV04Forcing:
    """Phase A: LLMObs.enable() forces the APM trace writer to v0.4."""

    def test_enable_forces_v04(self, tracer):
        """Even when DD_TRACE_API_VERSION=v0.5 is set, LLMObs.enable() recreates the writer
        as v0.4 because v0.5 strips meta_struct.
        """
        from ddtrace.internal.writer import NativeWriter

        # Pre-construct a v0.5 writer to simulate the user-configured default.
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
            # The aggregator's writer has been swapped to v0.4 by reset(llmobs_enabled=True).
            assert tracer._span_aggregator.writer._api_version == "v0.4"
            llmobs_service.disable()

    def test_native_writer_recreate_with_llmobs_kwarg(self):
        """NativeWriter.recreate(llmobs_enabled=True) returns a v0.4 writer regardless of
        the current api_version, mirroring the AppSec recreate behavior.
        """
        from ddtrace.internal.writer import NativeWriter

        writer = NativeWriter("http://dne:1234", api_version="v0.5")
        assert writer._api_version == "v0.5"
        new_writer = writer.recreate(llmobs_enabled=True)
        assert new_writer._api_version == "v0.4"

    def test_resolve_api_version_warns_on_explicit_v05(self, monkeypatch):
        """An explicit DD_TRACE_API_VERSION=v0.5 with LLMObs enabled gets downgraded and
        logged.
        """
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
    """The rescue processor sits between sampling and tags so it sees priority set by
    sampling_processor and runs before tags_processor mutates the span.
    """

    def test_chain_order_includes_llmobs_slot(self, tracer):
        agg = tracer._span_aggregator
        # Slot exists by default (as no-op) so the chain rebuild is free.
        assert agg.llmobs_fallback_processor is not None

    def test_default_slot_is_noop(self, tracer):
        from ddtrace._trace.processor import _NoopTraceProcessor

        # When LLMObs is not enabled the slot is a no-op identity processor.
        assert isinstance(tracer._span_aggregator.llmobs_fallback_processor, _NoopTraceProcessor)

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
            assert isinstance(tracer._span_aggregator.llmobs_fallback_processor, LLMObsSamplingFallbackProcessor)
            llmobs_service.disable()
            assert isinstance(tracer._span_aggregator.llmobs_fallback_processor, _NoopTraceProcessor)


class TestSamplingPriorityKeyPresent:
    """Sanity: the sampling priority value is stored on the context under the documented
    metric key so external assertions / dd-go behave the same.
    """

    def test_priority_round_trips(self, tracer):
        with tracer.trace("apm-span") as span:
            span.context.sampling_priority = USER_REJECT
            assert span.context._metrics[_SAMPLING_PRIORITY_KEY] == USER_REJECT
