import base64
import re
from typing import Any
from typing import Optional
from typing import Text

from ddtrace._trace._span_link import SpanLink
from ddtrace.constants import _ORIGIN_KEY
from ddtrace.constants import _SAMPLING_PRIORITY_KEY
from ddtrace.constants import _USER_ID_KEY
from ddtrace.internal.compat import NumericType
from ddtrace.internal.constants import MAX_UINT_64BITS as _MAX_UINT_64BITS
from ddtrace.internal.constants import W3C_TRACEPARENT_KEY
from ddtrace.internal.constants import W3C_TRACESTATE_KEY
from ddtrace.internal.logger import get_logger
from ddtrace.internal.threads import RLock
from ddtrace.internal.utils.http import w3c_get_dd_list_member as _w3c_get_dd_list_member


_ContextState = tuple[
    Optional[int],  # trace_id
    Optional[int],  # span_id
    dict[str, str],  # _meta
    dict[str, NumericType],  # _metrics
    list[SpanLink],  #  span_links
    dict[str, Any],  # baggage
    bool,  # is_remote
    bool,  # _reactivate
]


_DD_ORIGIN_INVALID_CHARS_REGEX = re.compile(r"[^\x20-\x7E]+")

log = get_logger(__name__)


class _VersionedMeta(dict[str, str]):
    """A dict that bumps a version counter on every mutation.

    ``Context._meta`` is shared by reference across every span in a local trace (see
    ``Context.copy``), and its ``_dd.p.*`` propagation subset is re-derived once per child
    span in ``Tracer.start_span`` (via ``_get_metas_to_propagate``) even though it is stable
    within a trace. Storing the derived value here, keyed on ``_v``, lets that filter run
    once per trace instead of once per span. Because *every* mutation bumps ``_v`` — including
    an in-place value change to ``_dd.p.dm`` from a sampling override, which does not change
    the dict's length — the cache invalidates correctly by construction. Cache reads fall back
    to recomputation for a plain ``dict`` (remote/propagation contexts), so behavior is
    unchanged there.
    """

    __slots__ = ("_v", "_prop_cache")

    _v: int
    _prop_cache: Optional[tuple[int, list[tuple[str, str]]]]

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        # dict(...) bulk-init does not route through __setitem__, so _v starts at 0.
        super().__init__(*args, **kwargs)
        self._v = 0
        self._prop_cache = None

    def __reduce__(self) -> Any:
        # Reconstruct through __init__ (bulk dict init) on unpickle. The default dict-subclass
        # reduce restores items via __setitem__ *before* the slot state, which would touch `_v`
        # before it exists; rebuilding from a plain dict initializes `_v`/`_prop_cache` correctly
        # (and drops the derived `_prop_cache`, which is recomputed on next access).
        return (self.__class__, (dict(self),))

    def _bump(self) -> None:
        self._v += 1
        self._prop_cache = None

    def __setitem__(self, key: str, value: str) -> None:
        super().__setitem__(key, value)
        self._v += 1
        self._prop_cache = None

    def __delitem__(self, key: str) -> None:
        super().__delitem__(key)
        self._bump()

    def clear(self) -> None:
        super().clear()
        self._bump()

    def pop(self, *args: Any) -> Any:
        r = super().pop(*args)
        self._bump()
        return r

    def popitem(self) -> tuple[str, str]:
        r = super().popitem()
        self._bump()
        return r

    def setdefault(self, key: str, default: str = "") -> str:
        n = len(self)
        r = super().setdefault(key, default)
        if len(self) != n:
            self._bump()
        return r

    def update(self, *args: Any, **kwargs: Any) -> None:
        super().update(*args, **kwargs)
        self._bump()

    # `dict.__or__` returns `dict`, so mypy flags the covariant `_VersionedMeta` return as
    # inconsistent; the override is behavior-correct (bump on `|=`), just rarely exercised.
    def __ior__(self, other: Any) -> "_VersionedMeta":  # type: ignore[override,misc]
        super().__ior__(other)
        self._bump()
        return self


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
        "_span_links",
        "_baggage",
        "_is_remote",
        "_reactivate",
        "__weakref__",
    ]

    def __init__(
        self,
        trace_id: Optional[int] = None,
        span_id: Optional[int] = None,
        dd_origin: Optional[str] = None,
        sampling_priority: Optional[float] = None,
        meta: Optional[dict[str, str]] = None,
        metrics: Optional[dict[str, NumericType]] = None,
        lock: Optional[RLock] = None,
        span_links: Optional[list[SpanLink]] = None,
        baggage: Optional[dict[str, Any]] = None,
        is_remote: bool = True,
    ):
        # PERF: local traces (meta is None here) get a version-tracking dict so the per-span
        # _dd.p.* propagation filter can be cached trace-wide (see _VersionedMeta). A caller-
        # supplied meta (remote/propagation) is used as-is to avoid changing its identity.
        self._meta: dict[str, str] = meta if meta is not None else _VersionedMeta()
        self._metrics: dict[str, NumericType] = metrics if metrics is not None else {}
        self._baggage: dict[str, Any] = baggage if baggage is not None else {}

        self.trace_id: Optional[int] = trace_id
        self.span_id: Optional[int] = span_id
        self._is_remote: bool = is_remote
        self._reactivate: bool = False

        if dd_origin is not None and _DD_ORIGIN_INVALID_CHARS_REGEX.search(dd_origin) is None:
            self._meta[_ORIGIN_KEY] = dd_origin
        if sampling_priority is not None:
            self._metrics[_SAMPLING_PRIORITY_KEY] = sampling_priority
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
            self._lock = RLock()

    def __getstate__(self) -> _ContextState:
        return (
            self.trace_id,
            self.span_id,
            self._meta,
            self._metrics,
            self._span_links,
            self._baggage,
            self._is_remote,
            self._reactivate,
            # Note: self._lock is not serializable
        )

    def __setstate__(self, state: _ContextState) -> None:
        (
            self.trace_id,
            self.span_id,
            self._meta,
            self._metrics,
            self._span_links,
            self._baggage,
            self._is_remote,
            self._reactivate,
        ) = state
        # We cannot serialize and lock, so we must recreate it unless we already have one
        self._lock = RLock()

    def __enter__(self) -> "Context":
        self._lock.acquire()
        return self

    def __exit__(self, *args: Any) -> None:
        self._lock.release()

    @property
    def sampling_priority(self) -> Optional[NumericType]:
        """Return the context sampling priority for the trace."""
        return self._metrics.get(_SAMPLING_PRIORITY_KEY)

    @sampling_priority.setter
    def sampling_priority(self, value: Optional[NumericType]) -> None:
        with self._lock:
            if value is None:
                if _SAMPLING_PRIORITY_KEY in self._metrics:
                    del self._metrics[_SAMPLING_PRIORITY_KEY]
                return
            self._metrics[_SAMPLING_PRIORITY_KEY] = value

    @property
    def _traceparent(self) -> str:
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
            trace_id = f"{self.trace_id:032x}"

        return f"00-{trace_id}-{self.span_id:016x}-{self._traceflags}"

    @property
    def _traceflags(self) -> str:
        return "01" if self.sampling_priority and self.sampling_priority > 0 else "00"

    @property
    def _tracestate(self) -> str:
        dd_list_member = _w3c_get_dd_list_member(self)

        # if there's a preexisting tracestate we need to update it to preserve other vendor data
        ts = self._meta.get(W3C_TRACESTATE_KEY, "")
        if ts and dd_list_member:
            # cut out the original dd list member from tracestate so we can replace it with the new one we created
            ts_w_out_dd = re.sub("dd=(.+?)(?:,|$)", "", ts)
            if ts_w_out_dd:
                ts = f"dd={dd_list_member},{ts_w_out_dd}"
            else:
                ts = f"dd={dd_list_member}"
        # if there is no original tracestate value then tracestate is just the dd list member we created
        elif dd_list_member:
            ts = f"dd={dd_list_member}"
        return ts

    @property
    def dd_origin(self) -> Optional[Text]:
        """Get the origin of the trace."""
        return self._meta.get(_ORIGIN_KEY)

    @dd_origin.setter
    def dd_origin(self, value: Optional[Text]) -> None:
        """Set the origin of the trace."""
        with self._lock:
            if value is None:
                if _ORIGIN_KEY in self._meta:
                    del self._meta[_ORIGIN_KEY]
                return
            self._meta[_ORIGIN_KEY] = value

    @property
    def dd_user_id(self) -> Optional[Text]:
        """Get the user ID of the trace."""
        user_id = self._meta.get(_USER_ID_KEY)
        if user_id:
            return str(base64.b64decode(user_id), encoding="utf-8")
        return None

    @dd_user_id.setter
    def dd_user_id(self, value: Optional[Text]) -> None:
        """Set the user ID of the trace."""
        with self._lock:
            if value is None:
                if _USER_ID_KEY in self._meta:
                    del self._meta[_USER_ID_KEY]
                return
            self._meta[_USER_ID_KEY] = str(base64.b64encode(bytes(value, encoding="utf-8")), encoding="utf-8")

    @property
    def _trace_id_64bits(self) -> Optional[int]:
        """Return the trace ID as a 64-bit value."""
        if self.trace_id is None:
            return None
        else:
            return _MAX_UINT_64BITS & self.trace_id

    def set_baggage_item(self, key: str, value: Any) -> None:
        """Sets a baggage item in this span context.
        Note that this operation mutates the baggage of this span context
        """
        self._baggage[key] = value

    def copy(self, trace_id: int, span_id: int) -> "Context":
        """Return a shallow copy of the context with the given correlation IDs."""
        # PERF: this runs once per child span. Build via __new__ + direct slot assignment to
        # skip __init__'s kwargs packing, the dd_origin regex, the sampling-priority branch,
        # and the default-{} allocations (meta/metrics/baggage are shared here). Behavior is
        # identical to __init__(trace_id, span_id, meta=..., metrics=..., lock=..., baggage=...,
        # is_remote=False): _reactivate defaults False and _span_links starts empty.
        ctx = Context.__new__(Context)
        ctx._meta = self._meta
        ctx._metrics = self._metrics
        ctx._baggage = self._baggage
        ctx._lock = self._lock
        ctx.trace_id = trace_id
        ctx.span_id = span_id
        ctx._is_remote = False
        ctx._reactivate = False
        ctx._span_links = []
        return ctx

    def _with_baggage_item(self, key: str, value: Any) -> "Context":
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

    def get_baggage_item(self, key: str) -> Optional[Any]:
        """Gets a baggage item in this span context."""
        return self._baggage.get(key, None)

    def get_all_baggage_items(self) -> dict[str, Any]:
        """Returns all baggage items in this span context."""
        return self._baggage

    def remove_baggage_item(self, key: str) -> None:
        """Remove a baggage item from this span context."""
        if key in self._baggage:
            del self._baggage[key]

    def remove_all_baggage_items(self) -> None:
        """Removes all baggage items from this span context."""
        self._baggage.clear()

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Context):
            with self._lock:
                return (
                    self.trace_id == other.trace_id
                    and self._meta == other._meta
                    and self._metrics == other._metrics
                    and self._span_links == other._span_links
                    and self._baggage == other._baggage
                    and self._is_remote == other._is_remote
                )
        return False

    def __repr__(self) -> str:
        return (
            f"Context(trace_id={self.trace_id}, span_id={self.span_id}, _meta={self._meta}, "
            f"_metrics={self._metrics}, _span_links={self._span_links}, _baggage={self._baggage}, "
            f"_is_remote={self._is_remote})"
        )

    def __hash__(self) -> int:
        return hash(self.trace_id)
