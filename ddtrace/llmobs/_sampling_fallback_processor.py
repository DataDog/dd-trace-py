"""Predicted-drop rescue for LLMObs span events that ride APM traces.

When LLM Observability is enabled in ``APM_AGENT_PROXY`` or ``APM_AGENTLESS`` mode the
LLMObs payload is attached to the APM span via ``meta_struct["_llmobs"]`` so it rides
the APM trace to intake, where the LLMObs processor extracts it. That path works as
long as the trace actually reaches intake.

When the SDK predicts the trace will be dropped (root ``sampling_priority <= 0``) the
Datadog Agent's local sampler can remove the chunk before it reaches trace-edge, and
client-side stats / libdatadog can drop the trace before the wire. To avoid losing the
LLMObs event in those cases this processor re-ships the cached event via the existing
``LLMObsSpanWriter`` and scrubs ``meta_struct["_llmobs"]`` from the APM span, then
stamps ``_dd.llmobs.submitted=1`` so the rescue can never fire twice for the same span
(idempotency) and so the writer-side intake can de-dup with the APM-side extract if the
Agent unexpectedly keeps the trace.

The processor never mutates ``sampling_priority`` and never adds sampling rules — LLM
Observability has zero impact on APM sampling decisions or billing.
"""

from typing import Optional

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs._constants import CACHED_LLMOBS_EVENT_CTX_KEY
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._constants import LLMOBS_SUBMITTED_TAG_KEY
from ddtrace.llmobs._writer import LLMObsSpanWriter


log = get_logger(__name__)


__all__ = ["LLMObsSamplingFallbackProcessor"]


class LLMObsSamplingFallbackProcessor(TraceProcessor):
    """Re-ships LLMObs events for spans whose APM trace the SDK predicts will be dropped.

    Inserted into ``SpanAggregator``'s hardcoded processor chain immediately after
    ``TraceSamplingProcessor`` and immediately before ``TraceTagsProcessor`` so the
    sampling decision has been computed and ``meta_struct["_llmobs"]`` is still present
    on the span.

    The processor relies on the cached event stashed by ``LLMObs._on_span_finish`` to
    avoid re-running the (potentially expensive) span-to-event conversion.
    """

    def __init__(self, llmobs_span_writer: LLMObsSpanWriter) -> None:
        super().__init__()
        self._llmobs_span_writer = llmobs_span_writer

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        # Always return the trace unchanged from the APM perspective; per-span mutations
        # only strip the LLMObs payload and stamp the idempotency tag.
        if not trace:
            return trace
        for span in trace:
            try:
                self._maybe_rescue(span)
            except Exception:
                log.debug(
                    "Failed to rescue LLMObs event for span %s; APM trace continues.",
                    span,
                    exc_info=True,
                )
        return trace

    def _maybe_rescue(self, span: Span) -> None:
        if span.span_type != SpanTypes.LLM:
            return
        # Idempotency: another path (e.g. LLMOBS_DIRECT hook) or a previous flush already
        # submitted this span.
        if span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1":
            return
        # Read priority from the local root so propagated upstream decisions are honored
        # (distributed-trace case where the upstream service set priority <= 0).
        root = span._local_root or span
        context = root.context
        priority = context.sampling_priority
        if priority is None or priority > 0:
            # No prediction available, or SDK plans to keep the trace — meta_struct rides
            # the APM trace and intake extracts the LLMObs payload. No rescue needed.
            return
        event = span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY)
        if event is None:
            # The hook either never built the event (kind not LLMObs) or the build failed.
            # No payload to rescue; ensure meta_struct stays scrubbed so we don't ship a
            # partial event to intake.
            span._remove_struct_tag(LLMOBS_STRUCT.KEY)
            return
        # Order matches the LLMOBS_DIRECT immediate-ship path in LLMObs._on_span_finish:
        # stamp the idempotency tag and scrub meta_struct *before* enqueue so that a writer
        # failure cannot leave a partial state where the APM trace still carries the
        # payload (would cause a duplicate via APM-side extract) without the tag intake
        # uses to de-dup.
        span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
        span._remove_struct_tag(LLMOBS_STRUCT.KEY)
        self._llmobs_span_writer.enqueue(event)
