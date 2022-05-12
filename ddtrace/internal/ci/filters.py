from typing import List
from typing import Optional
from typing import TYPE_CHECKING

import ddtrace
from ddtrace.ext import SpanTypes
from ddtrace.ext import ci
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

        # DEV: it might not be necessary to add library_version when using agentless mode
        local_root.set_tag(ci.LIBRARY_VERSION, ddtrace.__version__)
        return trace
