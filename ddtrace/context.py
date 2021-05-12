import threading
from typing import Optional
from typing import TYPE_CHECKING
from typing import Text

import attr

from .constants import ORIGIN_KEY
from .constants import SAMPLING_PRIORITY_KEY
from .internal.logger import get_logger


if TYPE_CHECKING:
    from .internal.compat import NumericType
    from .span import Span
    from .span import _MetaDictType
    from .span import _MetricDictType

log = get_logger(__name__)


@attr.s(eq=True, slots=True)
class Context(object):
    """Represents the state required to propagate a trace across process
    boundaries.
    """

    trace_id = attr.ib(default=None)  # type: Optional[int]
    span_id = attr.ib(default=None)  # type: Optional[int]
    _dd_origin = attr.ib(default=None)  # type: Optional[str]
    _sampling_priority = attr.ib(default=None)  # type: Optional[NumericType]
    _lock = attr.ib(factory=threading.RLock, eq=False)  # type: threading.Lock
    _meta = attr.ib(factory=dict)  # type: _MetaDictType
    _metrics = attr.ib(factory=dict)  # type: _MetricDictType

    def __attrs_post_init__(self):
        self.dd_origin = self._dd_origin
        self.sampling_priority = self._sampling_priority

    def _with_span(self, span):
        # type: (Span) -> Context
        """Return a shallow copy of the context with the given span."""
        with self._lock:
            ctx = Context(trace_id=span.trace_id, span_id=span.span_id)
            ctx._lock = self._lock
            ctx._meta = self._meta
            ctx._metrics = self._metrics
            return ctx

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
