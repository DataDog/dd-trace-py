import base64
import re
import threading
from typing import Any
from typing import Optional
from typing import TYPE_CHECKING
from typing import Text

import six

from .constants import ORIGIN_KEY
from .constants import SAMPLING_PRIORITY_KEY
from .constants import USER_ID_KEY
from .internal.compat import NumericType
from .internal.compat import PY2
from .internal.constants import _TRACEPARENT_KEY
from .internal.constants import _TRACESTATE_KEY
from .internal.logger import get_logger
from .internal.sampling import SAMPLING_DECISION_TRACE_TAG_KEY


if TYPE_CHECKING:  # pragma: no cover
    from typing import Tuple

    from .span import Span
    from .span import _MetaDictType
    from .span import _MetricDictType

    _ContextState = Tuple[
        Optional[int],  # trace_id
        Optional[int],  # span_id
        _MetaDictType,  # _meta
        _MetricDictType,  # _metrics
    ]

INVALID_TRACESTATE_TAG_VALUE_CHARS = r",|;|:|[^\x20-\x7E]+"

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
    def _traceparent(self):
        # type: () -> str
        tp = self._meta.get(_TRACEPARENT_KEY)
        # if we only have a traceparent then we'll forward it
        if tp and self.span_id is None:
            return tp

        if tp:
            # grab the original traceparent trace id, not the converted value
            trace_id = tp.split("-")[1]
        else:
            trace_id = "{:032x}".format(self.trace_id)

        sampled = 1 if self.sampling_priority and self.sampling_priority > 0 else 0
        return "00-{}-{:016x}-{:02x}".format(trace_id, self.span_id, sampled)

    @property
    def _tracestate(self):
        # type: () -> str
        dd = ""
        if self.sampling_priority:
            dd += "s:{};".format(self.sampling_priority)
        if self.dd_origin:
            dd += "o:{};".format(self.dd_origin)
        sampling_decision = self._meta.get(SAMPLING_DECISION_TRACE_TAG_KEY)
        if sampling_decision:
            # replace characters ",", "=", and characters outside the ASCII range 0x20 to 0x7E
            dd += "t.dm:{};".format(re.sub(INVALID_TRACESTATE_TAG_VALUE_CHARS, "_", sampling_decision))
        # since this can change, we need to grab the value off the current span
        usr_id_key = self._meta.get(USER_ID_KEY)
        if usr_id_key:
            dd += "t.usr.id:{};".format(re.sub(INVALID_TRACESTATE_TAG_VALUE_CHARS, "_", usr_id_key))

        # grab all other _dd.p values out of meta since we need to propagate all of them
        for k, v in self._meta.items():
            if (
                isinstance(k, six.string_types)
                and k.startswith("_dd.p")
                # we've already added sampling decision and user id
                and k not in [SAMPLING_DECISION_TRACE_TAG_KEY, USER_ID_KEY]
            ):
                # for key replace ",", "=", and characters outside the ASCII range 0x20 to 0x7E
                # for value replace ",", ";", ":" and characters outside the ASCII range 0x20 to 0x7E
                next_tag = "{}:{};".format(
                    re.sub("_dd.p.", "t.", re.sub(r",| |=|[^\x20-\x7E]+", "_", k)),
                    re.sub(INVALID_TRACESTATE_TAG_VALUE_CHARS, "_", v),
                )
                if not (len(dd) + len(next_tag)) > 256:
                    dd += next_tag
        # remove final ;
        if dd:
            dd = dd[:-1]

        # If there's a preexisting tracestate we need to update it to preserve other vendor data
        ts = self._meta.get(_TRACESTATE_KEY, "")
        if ts and dd:
            # cut out the original dd list member from tracestate so we can replace it with the new one we created
            ts_w_out_dd = re.sub("dd=(.+?)(?:,|$)", "", ts)
            if ts_w_out_dd:
                ts = "dd={},{}".format(dd, ts_w_out_dd)
            else:
                ts = "dd={}".format(dd)
        # if there is no original tracestate value then tracestate is just the dd list member we created
        elif dd:
            ts = "dd={}".format(dd)
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
