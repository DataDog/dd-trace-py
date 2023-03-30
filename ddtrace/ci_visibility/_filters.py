from typing import Dict
from typing import List
from typing import Optional
from typing import TYPE_CHECKING
from typing import Union

import ddtrace
from ddtrace.constants import AUTO_KEEP
from ddtrace.ext import SpanTypes
from ddtrace.ext import ci
from ddtrace.filters import TraceFilter


if TYPE_CHECKING:
    from ddtrace import Span


class TraceCiVisibilityFilter(TraceFilter):
    def __init__(self, tags, service):
        # type: (Dict[Union[str, bytes], str], str) -> None
        self._tags = tags
        self._service = service

    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        if not trace:
            return trace

        local_root = trace[0]._local_root
        if not local_root or local_root.span_type != SpanTypes.TEST:
            return None

        local_root.service = self._service
        local_root.context.dd_origin = ci.CI_APP_TEST_ORIGIN
        local_root.context.sampling_priority = AUTO_KEEP
        local_root.set_tags(self._tags)
        # DEV: it might not be necessary to add library_version when using agentless mode
        local_root.set_tag_str(ci.LIBRARY_VERSION, ddtrace.__version__)

        return trace
