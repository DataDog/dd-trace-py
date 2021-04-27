from typing import Optional
from typing import TYPE_CHECKING

from .internal.logger import get_logger


if TYPE_CHECKING:
    from .span import Span


log = get_logger(__name__)


class Context(object):
    """Represents a snapshot of a trace to be used to propagate a trace
    across execution boundaries (eg. distributed tracing).

    A Context contains the span_id of the active span at the time the context
    is created.
    """

    __slots__ = ["_trace_id", "_span_id", "_dd_origin", "_sampling_priority", "_span"]

    def __init__(
        self,
        trace_id=None,  # type: Optional[int]
        span_id=None,  # type: Optional[int]
        sampling_priority=None,  # type: Optional[float]
        dd_origin=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        self._trace_id = trace_id
        self._span_id = span_id
        self._dd_origin = dd_origin
        self._sampling_priority = sampling_priority

        # TODO[v1.0]: we need to keep a reference back to the span to maintain
        # backwards compatibility with setting sampling priority and origin on
        # the context
        # eg: span.context.sampling_priority = ...
        self._span = None  # type: Optional[Span]

    @property
    def trace_id(self):
        # type: () -> Optional[int]
        return self._trace_id

    @trace_id.setter
    def trace_id(self, value):
        # type: (int) -> None
        self._trace_id = value

    @property
    def span_id(self):
        # type: () -> Optional[int]
        return self._span_id

    @span_id.setter
    def span_id(self, value):
        # type: (int) -> None
        self._span_id = value

    @property
    def sampling_priority(self):
        # type: () -> Optional[float]
        if self._span:
            return self._span.sampling_priority
        return self._sampling_priority

    @sampling_priority.setter
    def sampling_priority(self, value):
        # type: (Optional[float]) -> None
        if self._span:
            self._span.sampling_priority = value
        self._sampling_priority = value

    @property
    def dd_origin(self):
        # type: () -> Optional[str]
        if self._span:
            return self._span.dd_origin
        return self._dd_origin

    @dd_origin.setter
    def dd_origin(self, value):
        # type: (Optional[str]) -> None
        if self._span:
            self._span.dd_origin = value
        self._dd_origin = value

    def __eq__(self, other):
        return (
            self.span_id == other.span_id
            and self.trace_id == other.trace_id
            and self.sampling_priority == other.sampling_priority
            and self.dd_origin == other.dd_origin
        )
