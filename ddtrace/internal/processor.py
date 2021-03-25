from typing import List
from typing import Optional

from ddtrace import Span
from ddtrace.filters import TraceFilter
from ddtrace.vendor import attr

from .logger import get_logger


log = get_logger(__name__)


@attr.s
class TraceProcessor(object):
    _filters = attr.ib(type=List[TraceFilter])

    def process(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        for filtr in self._filters:
            try:
                trace = filtr.process_trace(trace)
            except Exception:
                log.error("error applying filter %r to traces", filtr, exc_info=True)
            else:
                if trace is None:
                    log.debug("dropping trace due to filter %r", filtr)
                    return
        return trace
