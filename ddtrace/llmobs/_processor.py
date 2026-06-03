"""Centralized LLMObs export, wired to ``SpanAggregator.llmobs_processor``.

Runs at trace flush between ``TraceSamplingProcessor`` and ``TraceTagsProcessor``.

When APM tracing is on the payload rides the APM trace as ``meta_struct``. When it is off
(``DD_APM_TRACING_ENABLED=false`` or ``DD_TRACE_ENABLED=false``) there is no trace to ride, so
events ship via the writer and the APM trace is dropped (``process_trace`` returns ``None``).
"""

from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span
from ddtrace.ext import SpanTypes
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._config import config
from ddtrace.llmobs import _telemetry as telemetry
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._constants import LLMOBS_SUBMITTED_TAG_KEY
from ddtrace.llmobs._constants import LLMObsExportMode
from ddtrace.llmobs._utils import LLMObsSpan
from ddtrace.llmobs._utils import _build_llmobs_span
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import _normalize_llmobs_meta
from ddtrace.llmobs._utils import assemble_llmobs_span_event
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.llmobs._writer import LLMObsSpanWriter
from ddtrace.llmobs.types import _Meta
from ddtrace.llmobs.types import _MetaIO


if TYPE_CHECKING:
    from ddtrace.llmobs._writer import LLMObsSpanData


log = get_logger(__name__)

__all__ = ["LLMObsProcessor"]


class LLMObsProcessor(TraceProcessor):
    """Prepare, assemble, and submit LLMObs span events at trace flush.

    Holds discrete deps (writer, export mode, user processor, evaluator) rather than the
    ``LLMObs`` service instance.
    """

    def __init__(
        self,
        llmobs_span_writer: LLMObsSpanWriter,
        *,
        export_mode: LLMObsExportMode,
        user_span_processor: Optional[Callable[[LLMObsSpan], Optional[LLMObsSpan]]] = None,
        evaluator_runner: Any = None,
    ) -> None:
        super().__init__()
        self._llmobs_span_writer = llmobs_span_writer
        self._export_mode = export_mode
        self._user_span_processor = user_span_processor
        self._evaluator_runner = evaluator_runner

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        if not trace:
            return trace

        # No APM trace to ride when APM tracing or the tracer is disabled: ship via the writer
        # and drop the APM trace. LLMOBS_DIRECT is the export mode enable() selects from
        # DD_APM_TRACING_ENABLED, so it stands in for "APM tracing enabled".
        apm_tracing_enabled = self._export_mode != LLMObsExportMode.LLMOBS_DIRECT
        drop_apm_trace = not apm_tracing_enabled or not config._tracing_enabled

        for span in trace:
            if span.span_type != SpanTypes.LLM:
                continue
            try:
                self._process_span(span, ship_via_writer=drop_apm_trace)
            except Exception:
                log.debug("Failed to process LLMObs event for span %s; APM trace continues.", span, exc_info=True)

        if drop_apm_trace:
            return None
        return trace

    def _process_span(self, span: Span, ship_via_writer: bool) -> None:
        telemetry.record_span_created(span, self._export_mode, self._llmobs_span_writer._agentless)
        span_kind = get_llmobs_span_kind(span)
        try:
            llmobs_data = self._prepare_llmobs_span_data(span, span_kind)
        except (KeyError, TypeError, ValueError):
            log.error(
                "Error preparing LLMObs span data for span %s, likely due to malformed span",
                span,
                exc_info=True,
            )
            llmobs_data = None
        if llmobs_data is None:
            # Scrub so a partial payload never rides the APM trace.
            span._remove_struct_tag(LLMOBS_STRUCT.KEY)
            return

        try:
            event = assemble_llmobs_span_event(span, self._export_mode, llmobs_data)
        except (KeyError, TypeError, ValueError):
            log.error(
                "Error generating LLMObs span event for span %s, likely due to malformed span",
                span,
                exc_info=True,
            )
            event = None
        if event is None:
            span._remove_struct_tag(LLMOBS_STRUCT.KEY)
            return

        if self._evaluator_runner and span_kind == "llm":
            self._evaluator_runner.enqueue(event, span)

        if ship_via_writer:
            # Direct submission: mark the span so an APM-side read won't re-emit the event, then
            # scrub the payload before shipping via the writer.
            span.set_tag(LLMOBS_SUBMITTED_TAG_KEY, "1")
            span._remove_struct_tag(LLMOBS_STRUCT.KEY)
            self._llmobs_span_writer.enqueue(event)
            return
        # Otherwise the payload rides the APM trace as meta_struct.

    def _apply_user_span_processor(self, span: Span, llmobs_span: LLMObsSpan) -> Optional[LLMObsSpan]:
        """Returns the (possibly mutated) span, None to drop, or the original span on error."""
        if self._user_span_processor is None:
            return llmobs_span
        error = False
        try:
            llmobs_span._tags = get_llmobs_tags(span) or {}
            llmobs_span._tags["span.kind"] = get_llmobs_span_kind(span) or ""
            result = self._user_span_processor(llmobs_span)
            if result is None:
                return None
            if not isinstance(result, LLMObsSpan):
                raise TypeError("User span processor must return an LLMObsSpan or None, got %r" % type(result))
            return result
        except Exception as e:
            log.error("Error in LLMObs span processor (%r): %r", self._user_span_processor, e)
            error = True
            return llmobs_span
        finally:
            telemetry.record_llmobs_user_processor_called(error)

    def _prepare_llmobs_span_data(self, span: Span, span_kind: Optional[str]) -> Optional["LLMObsSpanData"]:
        """Finalize and return the live ``meta_struct`` dict in place, or None to skip export.

        Mutations (user processor, parent-prompt inheritance, error fields) fold into the dict
        ``_get_struct_tag`` returns; no re-set needed.
        """
        llmobs_data = _get_llmobs_data_metastruct(span)
        if not llmobs_data:
            log.error(
                "Error preparing LLMObs span event for span %s, missing LLMObs data in span context.",
                span,
            )
            return None
        if not span_kind:
            log.error(
                "Error preparing LLMObs span event for span %s, missing span kind in span context.",
                span,
            )
            return None

        llmobs_meta = llmobs_data.setdefault(LLMOBS_STRUCT.META, _Meta())
        llmobs_input = llmobs_meta.get(LLMOBS_STRUCT.INPUT) or _MetaIO()
        llmobs_output = llmobs_meta.get(LLMOBS_STRUCT.OUTPUT) or _MetaIO()

        llmobs_span, input_type, output_type = _build_llmobs_span(span_kind, llmobs_input, llmobs_output)
        user_processed_span = self._apply_user_span_processor(span, llmobs_span)
        if user_processed_span is None:
            log.debug("LLMObs span %s dropped by user processor", span)
            return None

        _normalize_llmobs_meta(
            span,
            user_processed_span,
            llmobs_meta,
            span_kind,
            input_type,
            output_type,
            export_to_llmobs=self._export_mode != LLMObsExportMode.APM_AGENTLESS,
        )
        if self._export_mode == LLMObsExportMode.APM_AGENTLESS:
            # APM agentless ingestion treats dots in tag keys as nested-path separators;
            # replace them with underscores before encoding.
            tags = {k.replace(".", "_"): v for k, v in llmobs_data.get(LLMOBS_STRUCT.TAGS, {}).items()}
            llmobs_data[LLMOBS_STRUCT.TAGS] = tags
        return llmobs_data
