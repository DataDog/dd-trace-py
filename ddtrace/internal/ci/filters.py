from typing import List
from typing import Optional
from typing import TYPE_CHECKING

from ddtrace.ext import SpanTypes
from ddtrace.filters import TraceFilter


if TYPE_CHECKING:
    from ddtrace import Span


class TraceCiVisibilityFilter(TraceFilter):
    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        if not trace:
            return trace

        local_root = trace[0]._local_root
        if not local_root or local_root.span_type != SpanTypes.TEST:
            return None

        return trace
