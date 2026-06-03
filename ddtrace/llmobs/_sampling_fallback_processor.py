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
    """Re-ships LLMObs events when the SDK predicts the APM trace will be dropped (APM_AGENT mode).

    Spans carrying ``meta_struct["_llmobs"]`` go to the local Agent, which drops unsampled spans
    (root ``sampling_priority <= 0``) before intake, taking the payload with them. On a predicted
    drop this re-ships the cached event via ``LLMObsSpanWriter`` and scrubs the meta_struct. It
    never mutates ``sampling_priority`` (no effect on APM sampling or billing).

    Must run after sampling finalizes the root priority and before later processors mutate the
    span (slotted between ``TraceSamplingProcessor`` and ``TraceTagsProcessor``). Relies on
    ``LLMObs._on_span_finish`` having cached the rendered event on the span.
    """

    def __init__(self, llmobs_span_writer: LLMObsSpanWriter) -> None:
        super().__init__()
        self._llmobs_span_writer = llmobs_span_writer

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        if not trace:
            return trace
        # One sampling decision per chunk; read it from the local root (the upstream reject
        # decision in distributed traces). priority > 0 is delivered intact, nothing to rescue.
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
