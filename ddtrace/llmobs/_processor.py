from typing import TYPE_CHECKING
from typing import Optional
from typing import cast

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
from ddtrace.llmobs._sampler import LLMObsSamplingResolver
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_trace_id
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

    def __init__(
        self,
        llmobs_span_writer: LLMObsSpanWriter,
        tracer: "Tracer",
        keep_meta_struct: bool = False,
        sampling_resolver: Optional[LLMObsSamplingResolver] = None,
    ) -> None:
        super().__init__()
        self._llmobs_span_writer = llmobs_span_writer
        self._tracer = tracer
        self._apm_tracing_enabled = asbool(env.get("DD_APM_TRACING_ENABLED", "true"))
        self._keep_meta_struct = keep_meta_struct
        self._sampling_resolver = sampling_resolver

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        drop_apm_trace = not self._apm_tracing_enabled or not self._tracer.enabled
        try:
            self._stamp_sampling_decisions(trace)
        except Exception:
            log.debug("Failed to stamp LLMObs sampling decisions.", exc_info=True)
        for span in trace:
            if span.span_type != SpanTypes.LLM:
                continue
            try:
                self._route_span(span, drop_apm_trace)
            except Exception:
                log.debug("Failed to route LLMObs event for span %s.", span, exc_info=True)
        if drop_apm_trace:
            return None
        return trace

    def _stamp_sampling_decisions(self, trace: list[Span]) -> None:
        """Resolve each LLMObs trace in this chunk and write its decision onto every span.

        This is the last point at which the decision can still be influenced by the root's tags.
        A trace the resolver cannot answer for is left as it is, keeping either the
        global-rate floor stamped at activation or a decision inherited from upstream.
        """
        if self._sampling_resolver is None:
            return

        groups: dict[str, list[Span]] = {}
        for span in trace:
            if span.span_type != SpanTypes.LLM:
                continue
            llmobs_trace_id = get_llmobs_trace_id(span)
            if llmobs_trace_id is not None:
                groups.setdefault(llmobs_trace_id, []).append(span)
        for spans in groups.values():
            # Any span in the group carries the pointer to the trace's root.
            sample_rate, sampling_decision = self._sampling_resolver.resolve(spans[0])
            if sample_rate is not None and sampling_decision is not None:
                for span in spans:
                    self._write_sampling_decision(span, sample_rate, sampling_decision)

    @staticmethod
    def _write_sampling_decision(span: Span, sample_rate: str, sampling_decision: str) -> None:
        """Write the decision into both places a span can be exported from.

        ``_llmobs_span_event`` shallow-copies the meta_struct ``_dd`` block into the event, so the
        two are independent dicts by now and ``_route_span`` sends one or the other depending on
        export mode. Writing only one silently loses the decision on the other path.
        """
        for dd in (
            _get_llmobs_data_metastruct(span).get(LLMOBS_STRUCT.DD),
            (span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY) or {}).get(LLMOBS_STRUCT.DD),
        ):
            if dd is None:
                continue
            dd[LLMOBS_STRUCT.SAMPLE_RATE] = sample_rate
            dd[LLMOBS_STRUCT.SAMPLING_DECISION] = sampling_decision

    def _scrub(self, span: Span) -> None:
        if not self._keep_meta_struct and span._get_struct_tag(LLMOBS_STRUCT.KEY) is not None:
            span._remove_struct_tag(LLMOBS_STRUCT.KEY)

    def _predicted_drop(self, span: Span) -> bool:
        # APM_AGENT only: the local agent drops traces whose root priority <= 0.
        root = span._local_root or span
        priority = root.context.sampling_priority
        return priority is not None and priority <= 0

    def _route_span(self, span: Span, drop_apm_trace: bool) -> None:
        event = span._get_ctx_item(CACHED_LLMOBS_EVENT_CTX_KEY)
        if event is None:
            # Half-built payload: scrub so a partial never rides the APM trace.
            self._scrub(span)
            return

        mode = cast(LLMObsExportMode, span._get_ctx_item(CACHED_LLMOBS_EXPORT_MODE_CTX_KEY))

        # The event rides the APM trace only when that trace is actually being sent
        # AND the mode keeps it on the trace (agentless = 100%, agent = kept priority).
        rides_trace = (
            not self._keep_meta_struct
            and not drop_apm_trace
            and (
                mode == LLMObsExportMode.APM_AGENTLESS
                or (mode == LLMObsExportMode.APM_AGENT and not self._predicted_drop(span))
            )
        )
        if not rides_trace:
            mode = (
                LLMObsExportMode.LLMOBS_AGENTLESS
                if self._llmobs_span_writer._agentless
                else LLMObsExportMode.LLMOBS_AGENT_PROXY
            )
            span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
            self._scrub(span)
            self._llmobs_span_writer.enqueue(event)
        telemetry.record_span_created(span, mode)
