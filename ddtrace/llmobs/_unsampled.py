from typing import Any
from typing import Optional

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span
from ddtrace.llmobs._constants import DD_LLMOBS_UNSAMPLED_TAG_KEY
from ddtrace.llmobs._constants import DROPPED_IO_COLLECTION_ERROR_UNSAMPLED
from ddtrace.llmobs._constants import LLMOBS_STRUCT


class LLMObsUnsampledStripProcessor(TraceProcessor):
    """Strip large input/output payloads from LLMObs span data when the trace
    has been sampled out by APM head-based sampling.

    The unsampled-LLMObs path forwards APM spans to intake even when sampling
    rejected the trace, so the backend can still emit token/cost metrics. The
    full input/output payloads are useless on that path (the spans are not
    indexed) and stripping them keeps the bandwidth cost down.

    Must run after ``TraceSamplingProcessor.process_trace``, since the
    sampling priority on the local-root span is finalized there.
    """

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        if not trace:
            return trace
        root = trace[0]._local_root or trace[0]
        # Priority is on the context here; the copy onto span._metrics happens later in the chain.
        priority = root.context.sampling_priority
        if priority is None or priority >= 1:
            return trace
        for span in trace:
            if not span._has_meta_structs():
                continue
            llmobs_data = span._get_struct_tag(LLMOBS_STRUCT.KEY)
            if not llmobs_data:
                continue
            _strip_llmobs_io(llmobs_data)
            span._set_struct_tag(LLMOBS_STRUCT.KEY, llmobs_data)
        return trace


def _strip_llmobs_io(llmobs_data: dict[str, Any]) -> None:
    meta = llmobs_data.get(LLMOBS_STRUCT.META)
    if isinstance(meta, dict):
        meta.pop(LLMOBS_STRUCT.INPUT, None)
        meta.pop(LLMOBS_STRUCT.OUTPUT, None)
        meta.pop(LLMOBS_STRUCT.EXPECTED_OUTPUT, None)
        meta.pop(LLMOBS_STRUCT.TOOL_DEFINITIONS, None)
        metadata = meta.setdefault(LLMOBS_STRUCT.METADATA, {})
        metadata_dd = metadata.setdefault(LLMOBS_STRUCT.METADATA_DD, {})
        errs = metadata_dd.setdefault("collection_errors", [])
        if DROPPED_IO_COLLECTION_ERROR_UNSAMPLED not in errs:
            errs.append(DROPPED_IO_COLLECTION_ERROR_UNSAMPLED)
    # Per-span marker so the backend can detect unsampled-for-metrics after trace-chunk fan-out.
    tags = llmobs_data.setdefault(LLMOBS_STRUCT.TAGS, {})
    tags[DD_LLMOBS_UNSAMPLED_TAG_KEY] = "1"
