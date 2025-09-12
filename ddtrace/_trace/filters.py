import abc
from typing import List
from typing import Optional

from ddtrace._trace.processor import TraceProcessor
from ddtrace._trace.span import Span


class TraceFilter(TraceProcessor):
    @abc.abstractmethod
    def process_trace(self, trace: List[Span]) -> Optional[List[Span]]:
        """Processes a trace.

        None can be returned to prevent the trace from being exported.
        """
        pass
