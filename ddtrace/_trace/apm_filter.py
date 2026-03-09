import os
from typing import Optional

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span
from ddtrace.internal.utils.formats import asbool


class APMTracingEnabledFilter(TraceProcessor):
    """
    Trace processor that:
      - drops all APM traces when DD_APM_TRACING_ENABLED is set to a falsy value.
      - scrubs meta_struct["_llmobs"] for any customer submitting LLMObs spans directly to LLMObs
    """

    def __init__(self) -> None:
        super().__init__()
        self._apm_tracing_enabled = asbool(os.getenv("DD_APM_TRACING_ENABLED", "true"))
        self._llmobs_export_mode = os.getenv("_DD_LLMOBS_EXPORT", "llmobs")

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        if not self._apm_tracing_enabled:
            return None
        if self._llmobs_export_mode == "llmobs":
            for span in trace:
                span._meta_struct.pop("_llmobs", None)
        return trace
