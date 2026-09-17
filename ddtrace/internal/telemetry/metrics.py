# -*- coding: utf-8 -*-
from enum import Enum
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Union
import weakref

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger

from .constants import TELEMETRY_NAMESPACE
from .constants import MetricTagType


if TYPE_CHECKING:
    from ddtrace.internal.native import MetricContext
    from ddtrace.internal.native import TelemetryWorker


log = get_logger(__name__)

_NATIVE_METRIC_ENUMS: Optional[dict[str, Any]] = None


def _native_metric_enums() -> dict[str, Any]:
    """Map the Python-side metric enums onto their native counterparts, once.

    Resolved lazily so that importing this module does not pull in the native extension.
    """
    global _NATIVE_METRIC_ENUMS
    if _NATIVE_METRIC_ENUMS is None:
        from ddtrace.internal.native import MetricNamespace
        from ddtrace.internal.native import MetricType

        _NATIVE_METRIC_ENUMS = {
            "namespace": {ns: getattr(MetricNamespace, ns.value) for ns in TELEMETRY_NAMESPACE},
            "metric_type": {
                "gauge": MetricType.gauge,
                "count": MetricType.count,
                "rate": MetricType.rate,
                "distribution": MetricType.distribution,
            },
        }
    return _NATIVE_METRIC_ENUMS


def _convert_metric_tags(tags: tuple[tuple[str, str], ...]) -> list[str]:
    """Convert the tag tuple form ((k, v), ...) into ["k:v", ...].

    Callers check for tags first and use the untagged add_point when there are none, so this
    is never handed an empty/None tag set.
    """
    return [f"{k}:{v}".lower() for k, v in tags]


def register_metric_context(
    worker: "TelemetryWorker",
    metric_type: str,
    namespace: TELEMETRY_NAMESPACE,
    name: str,
    tags: Optional[MetricTagType] = None,
) -> "MetricContext":
    """Register a metric context with worker and return its key.

    Contexts are worker-scoped: a key is only valid for the worker that issued it, so every worker
    rebuild invalidates the keys handed out by the previous one.

    Passing tags bakes them into the context, which only a MetricRecorder does - it owns one
    context for one fully-specified metric. The add_*_metric path registers without tags and
    sends them per point instead, so that varying tag values cannot grow its context cache without
    bound.
    """
    enums = _native_metric_enums()
    return worker.register_metric_context(
        enums["namespace"][namespace],
        # name may be an E(str, enum.Enum), use value then, rather than str().
        name.value if isinstance(name, Enum) else str(name),
        enums["metric_type"][metric_type],
        _convert_metric_tags(tags) if tags else [],
        # common marks language-shared metrics
        True,
    )


class _NoopWorker:
    """Stand-in worker so a recorder's hot path is unconditionally _worker.add_point(...).

    Recorders are bound to this until a native worker exists (and again once one goes away), which
    keeps the telemetry-disabled path a single call with no branching or rebinding attempts.
    """

    __slots__ = ()

    def add_point(self, context: Optional["MetricContext"], value: float) -> None:
        pass


_NOOP_WORKER = _NoopWorker()


class _PendingWorker:
    """Stand-in worker that registers the recorder's context on the first point recorded.

    Recorders are rebound from forksafe after-fork hooks, and registering natively from there
    is both wasteful (it lands on the fork critical path, for metrics the child may never record)
    and unsafe (native work inside the hook chain trips tokio's IO-safety check). Binding to this
    defers the registration to the first add, outside the fork window.
    """

    __slots__ = ("_recorder", "_worker")

    def __init__(self, recorder: "MetricRecorder", worker: "TelemetryWorker") -> None:
        self._recorder = recorder
        self._worker = worker

    def add_point(self, context: Optional["MetricContext"], value: float) -> None:
        recorder = self._recorder
        worker = self._worker
        with _recorder_lock:
            if recorder._worker is not self:
                return
            try:
                context = register_metric_context(
                    worker, recorder._metric_type, recorder._namespace, recorder._name, recorder._tags
                )
            except Exception:
                log.debug("Failed to register telemetry metric context for %s", recorder._name, exc_info=True)
                # Fall back to the noop rather than retrying the registration on every point.
                recorder._worker = _NOOP_WORKER
                return
            recorder._context = context
            recorder._worker = worker
        worker.add_point(context, value)


# Process-wide registry of MetricRecorders, doubling as the deduplicating cache behind
# get_metric_recorder(). Recorders are created at import time, long before (and independently of)
# the writer that ends up owning them, so they register here rather than with a writer instance;
# whichever writer builds a native worker adopts them all. Keyed by the full metric specification
# so two callers asking for the same metric share one native context instead of splitting their
# points across duplicates. Values are weak so a recorder whose owner is discarded (a Tracer, say)
# does not keep its context alive for the rest of the process.
_RecorderKey = tuple[TELEMETRY_NAMESPACE, str, str, MetricTagType]
_metric_recorders: "weakref.WeakValueDictionary[_RecorderKey, MetricRecorder]" = weakref.WeakValueDictionary()
# Guards the registry and every transition of a recorder's binding.
_recorder_lock = forksafe.Lock()
# Typed as object to avoid circular imports.
_active_writer: Optional[object] = None
_active_worker: Optional["TelemetryWorker"] = None


def get_metric_recorder(
    namespace: TELEMETRY_NAMESPACE,
    name: str,
    metric_type: str = "count",
    tags: Optional[MetricTagType] = None,
) -> "MetricRecorder":
    """Return the process-wide recorder for one fully-specified metric, creating it if needed."""
    key = (namespace, name, metric_type, tags)
    with _recorder_lock:
        recorder = _metric_recorders.get(key)
        if recorder is None:
            recorder = _metric_recorders[key] = MetricRecorder(namespace, name, metric_type, tags)
            if _active_worker is not None:
                recorder._rebind(_active_worker)
        return recorder


def _bind_metric_recorders(writer: object, worker: "TelemetryWorker") -> None:
    """Adopt every live recorder onto worker (called when a writer builds one)."""
    global _active_writer, _active_worker
    with _recorder_lock:
        _active_writer, _active_worker = writer, worker
        for recorder in list(_metric_recorders.values()):
            recorder._rebind(worker)


def _unbind_metric_recorders(writer: object) -> None:
    """Drop every recorder back to the noop worker when writer's worker goes away."""
    global _active_writer, _active_worker
    with _recorder_lock:
        if _active_writer is not None and _active_writer is not writer:
            # Another writer has since adopted the recorders (tests swap the process-wide writer);
            # this one tearing down must not unbind them.
            return
        _active_writer = _active_worker = None
        for recorder in list(_metric_recorders.values()):
            recorder._rebind(None)


class MetricRecorder:
    """Reusable handle for recording one telemetry metric from a hot path.

    add_*_metric spends most of its time finding the metric context: building the
    (namespace, name, type) cache key, hashing it and looking it up, then marshalling the tags
    for the point. A recorder is bound to one fully-specified metric - name and tags - so add
    is just a slot load and the native add_point, with no lookup at all:

        _EXECUTED_SINK_WEAK_HASH = get_metric_recorder(
            TELEMETRY_NAMESPACE.IAST, "executed.sink", tags=(("vulnerability_type", "WEAK_HASH"),)
        )
        _EXECUTED_SINK_WEAK_HASH.add()

    Go through get_metric_recorder rather than constructing one directly, so that callers
    asking for the same metric share a recorder instead of registering duplicate native contexts.

    Callers whose tag value varies at runtime keep their own {tag_value: recorder} mapping, so
    they pay a plain string-keyed lookup rather than building and hashing a tuple per point. Only do
    that for a bounded set of values - one recorder per value is retained for as long as the map is.

    Contexts belong to the native worker that issued them, so recorders are rebound whenever the
    writer rebuilds its worker.
    """

    __slots__ = ("_namespace", "_name", "_metric_type", "_tags", "_worker", "_context", "__weakref__")

    def __init__(
        self,
        namespace: TELEMETRY_NAMESPACE,
        name: str,
        metric_type: str = "count",
        tags: Optional[MetricTagType] = None,
    ) -> None:
        self._namespace = namespace
        self._name = name
        self._metric_type = metric_type
        self._tags = tags
        self._worker: Union["TelemetryWorker", _NoopWorker, _PendingWorker] = _NOOP_WORKER
        self._context: Optional["MetricContext"] = None

    def add(self, value: float = 1) -> None:
        """Record value against this recorder's metric."""
        # _context is None until the pending worker registers it; the noop and pending workers
        # both ignore what is passed. mypy can't see that pairing across the union.
        self._worker.add_point(self._context, value)  # type: ignore[arg-type]

    def _rebind(self, worker: Optional["TelemetryWorker"]) -> None:
        """Point at worker, whose context is registered on the next recorded point."""
        self._context = None
        self._worker = _NOOP_WORKER if worker is None else _PendingWorker(self, worker)
