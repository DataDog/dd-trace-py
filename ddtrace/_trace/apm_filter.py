from typing import Optional

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span


class APMTracingDisabledFilter(TraceProcessor):
    """Trace processor that drops every APM trace it sees.

    The decision to install this filter lives with the caller (e.g. LLMObs.enable()
    when ``DD_APM_TRACING_ENABLED`` is falsy or LLMObs is running agentless).
    """

    def process_trace(self, trace: list[Span]) -> Optional[list[Span]]:
        return None
