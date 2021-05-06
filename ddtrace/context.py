import threading
from typing import Optional
from typing import TYPE_CHECKING
from typing import Text

from .constants import ORIGIN_KEY
from .constants import SAMPLING_PRIORITY_KEY
from .internal.logger import get_logger


if TYPE_CHECKING:
    from .internal.compat import NumericType
    from .span import Span
    from .span import _MetaDictType
    from .span import _MetricDictType

log = get_logger(__name__)


class Context(object):
    """Represents the context of a trace.

    The context of a trace includes the active span id and trace-level tags.
    """

    __slots__ = ["trace_id", "span_id", "_lock", "_span", "_meta", "_metrics"]

    def __init__(
        self,
        trace_id=None,  # type: Optional[int]
        span_id=None,  # type: Optional[int]
        sampling_priority=None,  # type: Optional[NumericType]
        dd_origin=None,  # type: Optional[str]
    ):
        # type: (...) -> None
        self.trace_id = trace_id
        self.span_id = span_id

        self._lock = threading.RLock()
        # TODO[v1.0]: we need to keep a reference back to the span to maintain
        # backwards compatibility when using context as a parent.
        # eg:
        #    ctx = span.context
        #    tracer.start_span(child_of=ctx)
        self._span = None  # type: Optional[Span]
        self._meta = {}  # type: _MetaDictType
        self._metrics = {}  # type: _MetricDictType
        self.dd_origin = dd_origin
        self.sampling_priority = sampling_priority

    @property
    def sampling_priority(self):
        # type: () -> Optional[NumericType]
        """Return the context sampling priority for the trace."""
        return self._metrics.get(SAMPLING_PRIORITY_KEY)

    @sampling_priority.setter
    def sampling_priority(self, value):
        # type: (Optional[NumericType]) -> None
        with self._lock:
            if value is None:
                if SAMPLING_PRIORITY_KEY in self._metrics:
                    del self._metrics[SAMPLING_PRIORITY_KEY]
                return
            self._metrics[SAMPLING_PRIORITY_KEY] = value

    @property
    def dd_origin(self):
        # type: () -> Optional[Text]
        """Get the origin of the trace."""
        return self._meta.get(ORIGIN_KEY)

    @dd_origin.setter
    def dd_origin(self, value):
        # type: (Optional[Text]) -> None
        """Set the origin of the trace."""
        with self._lock:
            if value is None:
                if ORIGIN_KEY in self._meta:
                    del self._meta[ORIGIN_KEY]
                return
            self._meta[ORIGIN_KEY] = value

    def __eq__(self, other):
        return (
            self.span_id == other.span_id
            and self.trace_id == other.trace_id
            and self.sampling_priority == other.sampling_priority
            and self.dd_origin == other.dd_origin
            and self._meta == other._meta
            and self._metrics == other._metrics
        )
