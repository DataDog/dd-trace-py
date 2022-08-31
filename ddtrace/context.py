import base64
import threading
from typing import Any
from typing import Optional
from typing import TYPE_CHECKING
from typing import Text

from .constants import ORIGIN_KEY
from .constants import SAMPLING_PRIORITY_KEY
from .constants import USER_ID_KEY
from .internal.compat import NumericType
from .internal.compat import PY2
from .internal.logger import get_logger


if TYPE_CHECKING:
    from .span import Span
    from .span import _MetaDictType
    from .span import _MetricDictType

log = get_logger(__name__)


class Context(object):
    """Represents the state required to propagate a trace across execution
    boundaries.
    """

    __slots__ = [
        "trace_id",
        "span_id",
        "_lock",
        "_meta",
        "_metrics",
    ]

    def __init__(
        self,
        trace_id=None,  # type: Optional[int]
        span_id=None,  # type: Optional[int]
        dd_origin=None,  # type: Optional[str]
        sampling_priority=None,  # type: Optional[float]
        meta=None,  # type: Optional[_MetaDictType]
        metrics=None,  # type: Optional[_MetricDictType]
        lock=None,  # type: Optional[threading.RLock]
    ):
        self._meta = meta if meta is not None else {}  # type: _MetaDictType
        self._metrics = metrics if metrics is not None else {}  # type: _MetricDictType

        self.trace_id = trace_id  # type: Optional[int]
        self.span_id = span_id  # type: Optional[int]

        if dd_origin is not None:
            self._meta[ORIGIN_KEY] = dd_origin
        if sampling_priority is not None:
            self._metrics[SAMPLING_PRIORITY_KEY] = sampling_priority

        if lock is not None:
            self._lock = lock
        else:
            # DEV: A `forksafe.RLock` is not necessary here since Contexts
            # are recreated by the tracer after fork
            # https://github.com/DataDog/dd-trace-py/blob/a1932e8ddb704d259ea8a3188d30bf542f59fd8d/ddtrace/tracer.py#L489-L508
            self._lock = threading.RLock()

    def _with_span(self, span):
        # type: (Span) -> Context
        """Return a shallow copy of the context with the given span."""
        return self.__class__(
            trace_id=span.trace_id, span_id=span.span_id, meta=self._meta, metrics=self._metrics, lock=self._lock
        )

    def _update_tags(self, span):
        # type: (Span) -> None
        with self._lock:
            for tag in self._meta:
                span._meta.setdefault(tag, self._meta[tag])
            for metric in self._metrics:
                span._metrics.setdefault(metric, self._metrics[metric])

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

    @property
    def dd_user_id(self):
        # type: () -> Optional[Text]
        """Get the user ID of the trace."""
        user_id = self._meta.get(USER_ID_KEY)
        if user_id:
            if not PY2:
                return str(base64.b64decode(user_id), encoding="utf-8")
            else:
                return str(base64.b64decode(user_id))
        return None

    @dd_user_id.setter
    def dd_user_id(self, value):
        # type: (Optional[Text]) -> None
        """Set the user ID of the trace."""
        with self._lock:
            if value is None:
                if USER_ID_KEY in self._meta:
                    del self._meta[USER_ID_KEY]
                return
            if not PY2:
                value = str(base64.b64encode(bytes(value, encoding="utf-8")), encoding="utf-8")
            else:
                value = str(base64.b64encode(bytes(value)))
            self._meta[USER_ID_KEY] = value

    def __eq__(self, other):
        # type: (Any) -> bool
        if isinstance(other, Context):
            with self._lock:
                return (
                    self.trace_id == other.trace_id
                    and self.span_id == other.span_id
                    and self._meta == other._meta
                    and self._metrics == other._metrics
                )
        return False

    def __repr__(self):
        # type: () -> str
        return "Context(trace_id=%s, span_id=%s, _meta=%s, _metrics=%s)" % (
            self.trace_id,
            self.span_id,
            self._meta,
            self._metrics,
        )

    __str__ = __repr__
