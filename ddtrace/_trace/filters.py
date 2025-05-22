import abc
from typing import TYPE_CHECKING  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace._trace.processor import TraceProcessor


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace._trace.span import Span  # noqa:F401


class TraceFilter(TraceProcessor):
    @abc.abstractmethod
    def process_trace(self, trace):
        # type: (List[Span]) -> Optional[List[Span]]
        """Processes a trace.

        None can be returned to prevent the trace from being exported.
        """
        pass
