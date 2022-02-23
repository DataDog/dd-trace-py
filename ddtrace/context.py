import threading
from typing import Any
from typing import Optional
from typing import TYPE_CHECKING
from typing import Text

from .constants import ORIGIN_KEY
from .constants import SAMPLING_PRIORITY_KEY
from .internal.compat import NumericType
from .internal.constants import SamplingMechanism
from .internal.constants import UPSTREAM_SERVICES_KEY
from .internal.logger import get_logger
from .internal.utils.upstream_services import encode_upstream_service_entry


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
        "_upstream_services",
        "_self_upstream_service_entry",
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
        upstream_services=None,  # type: Optional[str]
    ):
        self._meta = meta if meta is not None else {}  # type: _MetaDictType
        self._metrics = metrics if metrics is not None else {}  # type: _MetricDictType

        # Keep track of:
        #   - Inherited upstream services from parent context
        #   - Upstream service entry associated with this context
        self._upstream_services = upstream_services
        if upstream_services and UPSTREAM_SERVICES_KEY not in self._meta:
            self._meta[UPSTREAM_SERVICES_KEY] = upstream_services
        self._self_upstream_service_entry = None

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
        ctx = self.__class__(
            trace_id=span.trace_id,
            span_id=span.span_id,
            meta=self._meta,
            metrics=self._metrics,
            lock=self._lock,
            upstream_services=self._upstream_services,
        )
        ctx._self_upstream_service_entry = self._self_upstream_service_entry
        return ctx

    def _update_tags(self, span):
        # type: (Span) -> None
        with self._lock:
            span._meta.update(self._meta)
            span._metrics.update(self._metrics)

    def _update_upstream_services(
        self,
        span,  # type: Span
        sampling_priority,  # type: int
        sampling_mechanism,  # type: SamplingMechanism
        sample_rate=None,  # type: Optional[float]
    ):
        # type: (...) -> None
        """
        Update the ``_dd.p.upstream_services`` tag associated with this context.

        Calling with the same values multiple times will not change anything.

        Calling multiple times with new values will replace the previous entry with the new one.
        (We will only have 1 entry in the tag for this context)
        """
        with self._lock:
            self._self_upstream_service_entry = encode_upstream_service_entry(
                span.service, sampling_priority, sampling_mechanism, sample_rate
            )

            # If we have inherited upstream service entries, append this entry onto those
            # DEV: We only want 1 entry in _dd.p.upstream_services for this context
            if self._upstream_services:
                self._meta[UPSTREAM_SERVICES_KEY] = ";".join(
                    [self._upstream_services, self._self_upstream_service_entry]
                )
            else:
                self._meta[UPSTREAM_SERVICES_KEY] = self._self_upstream_service_entry

    @property
    def upstream_services(self):
        # type: () -> Optional[str]
        return self._meta.get(UPSTREAM_SERVICES_KEY)

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
