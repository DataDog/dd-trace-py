from typing import TYPE_CHECKING
from typing import Optional

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool
from ddtrace.llmobs import _telemetry as telemetry
from ddtrace.llmobs._constants import CACHED_LLMOBS_EVENT_CTX_KEY
from ddtrace.llmobs._constants import CACHED_LLMOBS_EXPORT_MODE_CTX_KEY
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._constants import LLMOBS_SUBMITTED_TAG_KEY
from ddtrace.llmobs._constants import LLMObsExportMode
from ddtrace.llmobs._writer import LLMObsSpanWriter


if TYPE_CHECKING:
    from ddtrace._trace.tracer import Tracer


log = get_logger(__name__)


__all__ = ["LLMObsProcessor"]


class LLMObsProcessor(TraceProcessor):
    """Routes LLMObs span events to the correct intake and gates the APM trace.

    Single owner of:
      * per-span LLMObs export routing (mode and event are stamped on the span by
        ``LLMObs._on_span_finish``);
      * dropping the APM trace when either ``DD_APM_TRACING_ENABLED=false`` or the
        tracer is disabled at runtime (replaces the legacy ``APMTracingEnabledFilter``).
    """

    def __init__(self, llmobs_span_writer: LLMObsSpanWriter, tracer: "Tracer", keep_meta_struct: bool = False) -> None:
        super().__init__()
        self._llmobs_span_writer = llmobs_span_writer
        self._tracer = tracer
        self._apm_tracing_enabled = asbool(env.get("DD_APM_TRACING_ENABLED", "true"))
        self._keep_meta_struct = keep_meta_struct

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        drop_apm_trace = not self._apm_tracing_enabled or not self._tracer.enabled
        direct_mode = (
            LLMObsExportMode.LLMOBS_AGENTLESS
            if self._llmobs_span_writer._agentless
            else LLMObsExportMode.LLMOBS_AGENT_PROXY
        )
        for span in trace:
            if span.span_type != SpanTypes.LLM:
                continue
            try:
                self._route_span(span, drop_apm_trace, direct_mode)
            except Exception:
                log.debug("Failed to route LLMObs event for span %s.", span, exc_info=True)
        if drop_apm_trace:
            return None
        return trace

    def _route_span(self, span: Span, drop_apm_trace: bool, direct_mode: LLMObsExportMode) -> None:
        mode = span._get_ctx_item(CACHED_LLMOBS_EXPORT_MODE_CTX_KEY)
        event = span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY)
        # APM modes can't ride a dropped trace; rewrite to the writer's transport.
        if drop_apm_trace and mode in (LLMObsExportMode.APM_AGENT, LLMObsExportMode.APM_AGENTLESS):
            mode = direct_mode

        if event is None:
            # Half-built payload (build failed mid-annotation): scrub so it never rides the APM trace.
            if not self._keep_meta_struct and span._get_struct_tag(LLMOBS_STRUCT.KEY) is not None:
                span._remove_struct_tag(LLMOBS_STRUCT.KEY)
            return

        already_submitted = span.get_tag(LLMOBS_SUBMITTED_TAG_KEY) == "1"
        if already_submitted:
            return

        if self._keep_meta_struct:
            # Test mode: always enqueue without scrubbing meta_struct.
            self._llmobs_span_writer.enqueue(event)
            span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
            return

        if mode in (LLMObsExportMode.LLMOBS_AGENT_PROXY, LLMObsExportMode.LLMOBS_AGENTLESS):
            telemetry.record_span_created(span, mode)
            span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
            span._remove_struct_tag(LLMOBS_STRUCT.KEY)
            self._llmobs_span_writer.enqueue(event)
        elif mode == LLMObsExportMode.APM_AGENT:
            root = span._local_root or span
            priority = root.context.sampling_priority
            if priority is not None and priority <= 0:
                telemetry.record_span_created(span, direct_mode)
                span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
                span._remove_struct_tag(LLMOBS_STRUCT.KEY)
                self._llmobs_span_writer.enqueue(event)
            else:
                telemetry.record_span_created(span, mode)
        elif mode == LLMObsExportMode.APM_AGENTLESS:
            telemetry.record_span_created(span, mode)
        elif mode is not None:
            log.debug("Skipping LLMObs event for span %s. Unexpected mode: %s", span, mode)
