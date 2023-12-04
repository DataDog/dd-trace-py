import base64
import re
import threading
from typing import TYPE_CHECKING  # noqa:F401
from typing import Any  # noqa:F401
from typing import Optional
from typing import Text  # noqa:F401

from ddtrace.tracing._span_link import SpanLink  # noqa:F401

from .constants import ORIGIN_KEY
from .constants import SAMPLING_PRIORITY_KEY
from .constants import USER_ID_KEY
from .internal.compat import NumericType
from .internal.constants import W3C_TRACEPARENT_KEY
from .internal.constants import W3C_TRACESTATE_KEY
from .internal.logger import get_logger
from .internal.utils.http import w3c_get_dd_list_member as _w3c_get_dd_list_member


if TYPE_CHECKING:  # pragma: no cover
    from typing import Tuple  # noqa:F401,I001

    from .span import Span  # noqa:F401

    from .span import _MetaDictType
    from .span import _MetricDictType

    _ContextState = Tuple[
        Optional[int],  # trace_id
        Optional[int],  # span_id
        _MetaDictType,  # _meta
        _MetricDictType,  # _metrics
    ]


_DD_ORIGIN_INVALID_CHARS_REGEX = re.compile(r"[^\x20-\x7E]+")

log = get_logger(__name__)


class Context(object):
    """Represents the state required to propagate a trace across execution
    boundaries.
    """

    __slots__ = ["trace_id", "span_id", "_lock", "_meta", "_metrics", "_span_links", "_baggage"]

    def __init__(
        self,
        trace_id=None,  # type: Optional[int]
        span_id=None,  # type: Optional[int]
        dd_origin=None,  # type: Optional[str]
        sampling_priority=None,  # type: Optional[float]
        meta=None,  # type: Optional[_MetaDictType]
        metrics=None,  # type: Optional[_MetricDictType]
        lock=None,  # type: Optional[threading.RLock]
        span_links=None,  # type: Optional[list[SpanLink]]
        baggage=None,  # type: Optional[dict[str, Any]]
    ):
        self._meta = meta if meta is not None else {}  # type: _MetaDictType
        self._metrics = metrics if metrics is not None else {}  # type: _MetricDictType
        self._baggage = baggage if baggage is not None else {}  # type: dict[str, Any]

        self.trace_id = trace_id  # type: Optional[int]
        self.span_id = span_id  # type: Optional[int]

        if dd_origin is not None and _DD_ORIGIN_INVALID_CHARS_REGEX.search(dd_origin) is None:
            self._meta[ORIGIN_KEY] = dd_origin
        if sampling_priority is not None:
            self._metrics[SAMPLING_PRIORITY_KEY] = sampling_priority
        if span_links is not None:
            self._span_links = span_links
        else:
            self._span_links = []

        if lock is not None:
            self._lock = lock
        else:
            # DEV: A `forksafe.RLock` is not necessary here since Contexts
            # are recreated by the tracer after fork
            # https://github.com/DataDog/dd-trace-py/blob/a1932e8ddb704d259ea8a3188d30bf542f59fd8d/ddtrace/tracer.py#L489-L508
            self._lock = threading.RLock()

    def __getstate__(self):
        # type: () -> _ContextState
        return (
            self.trace_id,
            self.span_id,
            self._meta,
            self._metrics,
            # Note: self._lock is not serializable
        )

    def __setstate__(self, state):
        # type: (_ContextState) -> None
        self.trace_id, self.span_id, self._meta, self._metrics = state
        # We cannot serialize and lock, so we must recreate it unless we already have one
        self._lock = threading.RLock()

    def _with_span(self, span):
        # type: (Span) -> Context
        """Return a shallow copy of the context with the given span."""
        return self.__class__(
            trace_id=span.trace_id,
            span_id=span.span_id,
            meta=self._meta,
            metrics=self._metrics,
            lock=self._lock,
            baggage=self._baggage,
        )

    def _update_tags(self, span):
        # type: (Span) -> None
        with self._lock:
            for tag in self._meta:
                span._meta.setdefault(tag, self._meta[tag])
            for metric in self._metrics:
                span._metrics.setdefault(metric, self._metrics[metric])

    @property
    def sampling_priority(self) -> Optional[NumericType]:
        """Return the context sampling priority for the trace."""
        return self._metrics.get(SAMPLING_PRIORITY_KEY)

    @sampling_priority.setter
    def sampling_priority(self, value: Optional[NumericType]) -> None:
        with self._lock:
            if value is None:
                if SAMPLING_PRIORITY_KEY in self._metrics:
                    del self._metrics[SAMPLING_PRIORITY_KEY]
                return
            self._metrics[SAMPLING_PRIORITY_KEY] = value

    @property
    def _traceparent(self):
        # type: () -> str
        tp = self._meta.get(W3C_TRACEPARENT_KEY)
        if self.span_id is None or self.trace_id is None:
            # if we only have a traceparent then we'll forward it
            # if we don't have a span id or trace id value we can't build a valid traceparent
            return tp or ""

        # determine the trace_id value
        if tp:
            # grab the original traceparent trace id, not the converted value
            trace_id = tp.split("-")[1]
        else:
            trace_id = "{:032x}".format(self.trace_id)

        return "00-{}-{:016x}-{}".format(trace_id, self.span_id, self._traceflags)

    @property
    def _traceflags(self):
        # type: () -> str
        return "01" if self.sampling_priority and self.sampling_priority > 0 else "00"

    @property
    def _tracestate(self):
        # type: () -> str
        dd_list_member = _w3c_get_dd_list_member(self)

        # if there's a preexisting tracestate we need to update it to preserve other vendor data
        ts = self._meta.get(W3C_TRACESTATE_KEY, "")
        if ts and dd_list_member:
            # cut out the original dd list member from tracestate so we can replace it with the new one we created
            ts_w_out_dd = re.sub("dd=(.+?)(?:,|$)", "", ts)
            if ts_w_out_dd:
                ts = "dd={},{}".format(dd_list_member, ts_w_out_dd)
            else:
                ts = "dd={}".format(dd_list_member)
        # if there is no original tracestate value then tracestate is just the dd list member we created
        elif dd_list_member:
            ts = "dd={}".format(dd_list_member)
        return ts

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
            return str(base64.b64decode(user_id), encoding="utf-8")
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
            self._meta[USER_ID_KEY] = str(base64.b64encode(bytes(value, encoding="utf-8")), encoding="utf-8")

    def _set_baggage_item(self, key, value):
        # type: (str, Any) -> None
        """Sets a baggage item in this span context.
        Note that this operation mutates the baggage of this span context
        """
        self._baggage[key] = value

    def _with_baggage_item(self, key, value):
        # type: (str, Any) -> Context
        """Returns a copy of this span with a new baggage item.
        Useful for instantiating new child span contexts.
        """
        new_baggage = dict(self._baggage)
        new_baggage[key] = value

        ctx = self.__class__(trace_id=self.trace_id, span_id=self.span_id)
        ctx._meta = self._meta
        ctx._metrics = self._metrics
        ctx._baggage = new_baggage
        return ctx

    def _get_baggage_item(self, key):
        # type: (str) -> Optional[Any]
        """Gets a baggage item in this span context."""
        return self._baggage.get(key, None)

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
        return "Context(trace_id=%s, span_id=%s, _meta=%s, _metrics=%s, _span_links=%s)" % (
            self.trace_id,
            self.span_id,
            self._meta,
            self._metrics,
            self._span_links,
        )

    __str__ = __repr__
