"""Profiling owns endpoint aggregation and upload-window rotation."""

from typing import Optional
from weakref import WeakKeyDictionary
from weakref import finalize

from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal import forksafe
from ddtrace.internal.span_lifecycle import SpanCompletedEvent


EndpointData = tuple[dict[str, int], dict[str, list[int]]]


class EndpointCounter:
    def __init__(self, source: object) -> None:
        self._source_id = id(source)
        self._lock = forksafe.Lock()
        self._enabled = False
        self._finalizer: Optional[finalize[[], object]] = None
        self._counts: dict[str, int] = {}
        # Mapping from endpoint to list of span IDs avoids copying the resource per span.
        self._span_ids: dict[str, list[int]] = {}

    def start(self) -> None:
        if not self._enabled:
            self._enabled = True
            core.on(SpanCompletedEvent.event_name, self._on_completed)

    def stop(self) -> None:
        if self._finalizer is not None:
            self._finalizer.detach()
            self._finalizer = None
        core.reset_listeners(SpanCompletedEvent.event_name, self._on_completed)
        with self._lock:
            self._enabled = False
            self._counts = {}
            self._span_ids = {}

    def _on_completed(self, event: SpanCompletedEvent) -> None:
        span = event.span
        if (
            span.source_id != self._source_id
            or not span.recording
            or not span.is_local_root
            or span.span_type != SpanTypes.WEB
        ):
            return
        with self._lock:
            if self._enabled:
                self._counts[span.resource] = self._counts.get(span.resource, 0) + 1
                self._span_ids.setdefault(span.resource, []).append(span.span_id)

    def reset(self) -> EndpointData:
        with self._lock:
            result = self._counts, self._span_ids
            self._counts, self._span_ids = {}, {}
            return result


_counters: WeakKeyDictionary[object, EndpointCounter] = WeakKeyDictionary()
_registry_lock = forksafe.Lock()


def enable(source: object) -> None:
    with _registry_lock:
        if source not in _counters:
            counter = EndpointCounter(source)
            counter.start()
            _counters[source] = counter
            counter._finalizer = finalize(source, counter.stop)


def disable(source: object) -> None:
    with _registry_lock:
        counter = _counters.pop(source, None)
        if counter is not None:
            counter.stop()


def reset(source: Optional[object]) -> EndpointData:
    if source is None:
        return {}, {}
    with _registry_lock:
        counter = _counters.get(source)
        return ({}, {}) if counter is None else counter.reset()


@forksafe.register
def _after_fork() -> None:
    # Fork-safe locks have already been recreated; parent profile windows must not leak into the child.
    for counter in list(_counters.values()):
        counter.reset()
