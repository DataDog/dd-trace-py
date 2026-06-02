"""Predicted-drop rescue for LLMObs payloads riding APM traces in APM_AGENT mode.

Only the Agent path needs this. Spans carrying ``meta_struct["_llmobs"]`` are sent to the
local Agent, which drops unsampled spans (root ``sampling_priority <= 0``) before they reach
intake, taking the payload with them. This processor re-ships the cached event via
``LLMObsSpanWriter``, scrubs the meta_struct entry, and stamps ``_dd.llmobs.submitted=1`` so
intake-side dedup can drop the APM extract if the Agent unexpectedly keeps the trace.

Agentless export ships straight to intake at 100% (no Agent to drop it) and is never routed
through this processor; LLMOBS_DIRECT ships via the writer at span finish.

The processor never mutates ``sampling_priority`` — LLMObs has no influence on APM sampling
or billing.
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
    """Re-ships LLMObs events when the SDK predicts the APM trace will be dropped.

    Slotted between ``TraceSamplingProcessor`` and ``TraceTagsProcessor`` in
    ``SpanAggregator``'s chain: it must run after sampling has finalized the root priority
    (so the predicted-drop check is accurate) and before later processors further mutate
    the span.

    Relies on ``LLMObs._on_span_finish`` having cached the rendered event on the span
    (avoids re-running the span-to-event conversion).
    """

    def __init__(self, llmobs_span_writer: LLMObsSpanWriter) -> None:
        super().__init__()
        self._llmobs_span_writer = llmobs_span_writer

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        if not trace:
            return trace
        # The whole chunk shares one sampling decision; read it once from the local root. In
        # distributed traces the upstream service's reject decision is what reaches the Agent.
        # A predicted-kept trace (priority > 0) is delivered with its meta_struct intact, so
        # there is nothing to rescue and we skip the per-span work entirely.
        root = trace[0]._local_root or trace[0]
        priority = root.context.sampling_priority
        if priority is None or priority > 0:
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
        if span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1":
            return
        event = span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY)
        if event is None:
            # No event was built (non-LLMObs span_kind or build failure). Scrub any
            # half-built payload so it never rides the APM trace partial.
            span._remove_struct_tag(LLMOBS_STRUCT.KEY)
            return
        # Tag + scrub before enqueue: a writer failure must not leave the payload on
        # the span (APM-side extract would duplicate without the dedup tag).
        span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
        span._remove_struct_tag(LLMOBS_STRUCT.KEY)
        self._llmobs_span_writer.enqueue(event)
