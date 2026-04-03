from typing import List
from typing import Optional

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span


class LLMObsBridgeTagsProcessor(TraceProcessor):
    """Propagate ``llmobs_trace_id`` / ``llmobs_parent_id`` from the local root to every
    span in the batch.

    Without this, partial-flush payloads that do not contain the local root carry no
    bridge tags and the backend trace-indexer generates a fresh LLMObs trace ID,
    disconnecting OTel gen_ai spans from the correct LLMObs trace.
    """

    def process_trace(self, trace: List[Span]) -> Optional[List[Span]]:
        for span in trace:
            if span.get_tag("llmobs_trace_id") is None:
                local_root = span._local_root
                llmobs_trace_id = local_root.get_tag("llmobs_trace_id")
                if llmobs_trace_id is not None:
                    span.set_tag("llmobs_trace_id", llmobs_trace_id)
                    span.set_tag("llmobs_parent_id", local_root.get_tag("llmobs_parent_id"))
        return trace
