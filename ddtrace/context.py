import threading
from typing import Optional
from typing import TYPE_CHECKING
from typing import Text

import attr

from .constants import ORIGIN_KEY
from .constants import SAMPLING_PRIORITY_KEY
from .internal.compat import NumericType
from .internal.logger import get_logger
from .utils.deprecation import deprecated


if TYPE_CHECKING:
    from .span import Span
    from .span import _MetaDictType
    from .span import _MetricDictType

log = get_logger(__name__)


@attr.s(eq=False, slots=True)
class Context(object):
    """Represents the state required to propagate a trace across execution
    boundaries.
    """

    trace_id = attr.ib(default=None, type=Optional[int])
    span_id = attr.ib(default=None, type=Optional[int])
    _dd_origin = attr.ib(default=None, type=Optional[str], repr=False)
    _sampling_priority = attr.ib(default=None, type=Optional[NumericType], repr=False)
    _lock = attr.ib(factory=threading.RLock, type=threading.RLock, repr=False)
    _meta = attr.ib(factory=dict)  # type: _MetaDictType
    _metrics = attr.ib(factory=dict)  # type: _MetricDictType

    def __attrs_post_init__(self):
        if self._dd_origin is not None:
            self.dd_origin = self._dd_origin
        if self._sampling_priority is not None:
            self.sampling_priority = self._sampling_priority
        del self._dd_origin
        del self._sampling_priority

    def __eq__(self, other):
        with self._lock:
            return (
                self.trace_id == other.trace_id
                and self.span_id == other.span_id
                and self._meta == other._meta
                and self._metrics == other._metrics
            )

    def _with_span(self, span):
        # type: (Span) -> Context
        """Return a shallow copy of the context with the given span."""
        ctx = self.__class__(trace_id=span.trace_id, span_id=span.span_id)
        ctx._lock = self._lock
        ctx._meta = self._meta
        ctx._metrics = self._metrics
        return ctx

    def _update_tags(self, span):
        # type: (Span) -> None
        with self._lock:
            span.meta.update(self._meta)
            span.metrics.update(self._metrics)

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

    @deprecated("Cloning contexts will no longer be required in 0.50", version="0.50")
    def clone(self):
        # type: () -> Context
        """
        Partially clones the current context.
        It copies everything EXCEPT the registered and finished spans.
        """
        return self
