from __future__ import annotations

import _thread
import gc
import logging
import time
from typing import Callable
from typing import Optional

from ddtrace.internal import forksafe
from ddtrace.internal._unpatched import threading_Lock
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.runtime.gc_monitor import GCPauseMonitor
from ddtrace.internal.runtime.gc_monitor import PauseListener
from ddtrace.internal.runtime.gc_monitor import gc_pause_monitor
from ddtrace.internal.settings.profiling import config
from ddtrace.profiling import _threading
from ddtrace.profiling import collector


LOG = logging.getLogger(__name__)

_GEN_NAMES: tuple[str, ...] = (
    "gc.collect[gen=0]",
    "gc.collect[gen=1]",
    "gc.collect[gen=2]",
)
_GEN_COUNT: int = len(_GEN_NAMES)
_GC_CONFIG_FRAME: str = "gc.config"
_GC_FILE: str = "gc"
# Cap on individually reported pauses per flush interval. Long pauses past the
# cap fall back to the per-generation window totals.
_MAX_PAUSE_EVENTS: int = 256
# String "gc event" label value: this sample is one pause (not a window total).
# Backend Timeline filter keys on getStringLabel("gc event") == "pause".
_GC_EVENT_PAUSE: str = "pause"


class GCCollector(collector.Collector):
    """Collect CPython GC pause durations and explicit gc.collect() call counts.

    Subscribes to the process-wide GCPauseMonitor so runtime metrics and
    profiling share one gc.callbacks entry. Emits samples once per profile
    flush interval.

    Data emitted:
    - GC time samples (push_gctime) attributed to synthetic gc.collect[gen=N]
      frames. These appear as libdatadog gc-time / gc-samples (GC Time view).
      The window sum is pushed with count=1 (push_gctime multiplies ns*count,
      same as walltime), then remaining pause counts are added with zero
      duration so gc-samples matches the pause count.
    - Pauses at or above DD_PROFILING_GC_PAUSE_EVENT_THRESHOLD_MS are also
      emitted one sample each, with an end timestamp, thread labels, a
      numeric "gc generation" label, and string "gc event"="pause" so the
      backend can tell a single pause from a window total. Their time is
      not also added to the window totals. Events past the per-interval
      cap fall back to the totals. The callback only writes into a
      preallocated ring; thread name and native id are resolved at
      snapshot() time.
    - A gc.config snapshot sample per flush carrying the interval's explicit
      gc.collect() tally in the native gc-samples field.

    Thresholds, freeze count, and cumulative collection totals are DEBUG-only.
    """

    _lock: threading_Lock
    _pause_count: list[int]
    _pause_total_ns: list[int]
    _event_gen: list[int]
    _event_pause_ns: list[int]
    _event_end_ns: list[int]
    _event_thread_id: list[int]
    _event_count: int
    _snap_event_gen: list[int]
    _snap_event_pause_ns: list[int]
    _snap_event_end_ns: list[int]
    _snap_event_thread_id: list[int]
    _event_threshold_ns: int
    _explicit_count: int
    _orig_collect: Callable[..., int]
    _installed_collect: Optional[Callable[..., int]]
    _patch_active: bool
    _pause_listener: Optional[PauseListener]
    _monitor: Optional[GCPauseMonitor]

    def __init__(self, pause_event_threshold_ms: Optional[float] = None, *args: object, **kwargs: object) -> None:
        super().__init__(*args, **kwargs)
        threshold_ms: float = (
            pause_event_threshold_ms if pause_event_threshold_ms is not None else config.gc.pause_event_threshold_ms
        )
        self._event_threshold_ns: int = int(threshold_ms * 1_000_000)

    def _start_service(self) -> None:
        self._lock = forksafe.Lock()
        self._pause_count = [0] * _GEN_COUNT
        self._pause_total_ns = [0] * _GEN_COUNT
        # Preallocate the pause-event ring once. _on_pause must not allocate.
        self._event_gen = [0] * _MAX_PAUSE_EVENTS
        self._event_pause_ns = [0] * _MAX_PAUSE_EVENTS
        self._event_end_ns = [0] * _MAX_PAUSE_EVENTS
        self._event_thread_id = [0] * _MAX_PAUSE_EVENTS
        self._event_count = 0
        # Snapshot scratch mirrors the ring so we never slice/allocate under
        # the lock (a GC pause while we hold it would be dropped).
        self._snap_event_gen = [0] * _MAX_PAUSE_EVENTS
        self._snap_event_pause_ns = [0] * _MAX_PAUSE_EVENTS
        self._snap_event_end_ns = [0] * _MAX_PAUSE_EVENTS
        self._snap_event_thread_id = [0] * _MAX_PAUSE_EVENTS
        self._explicit_count = 0
        # Set before publishing gc.collect so a concurrent/reentrant caller
        # into _patched_collect never sees a missing attribute.
        self._patch_active = True
        # Bound-method lookups allocate a new object each time; cache once so
        # teardown can identity-check the installed callable and so
        # remove_listener finds the same object add_listener registered.
        installed: Optional[Callable[..., int]] = getattr(self, "_installed_collect", None)
        if installed is None:
            self._orig_collect = gc.collect
            self._installed_collect = self._patched_collect
            gc.collect = self._installed_collect
        self._pause_listener = self._on_pause
        self._monitor = gc_pause_monitor()
        self._monitor.add_listener(self._pause_listener)
        self._monitor.acquire()
        forksafe.register(self._reset_after_fork)
        LOG.debug("GCCollector started")

    def _stop_service(self) -> None:
        self._patch_active = False
        monitor: Optional[GCPauseMonitor] = self._monitor
        pause_listener: Optional[PauseListener] = getattr(self, "_pause_listener", None)
        if monitor is not None and pause_listener is not None:
            monitor.remove_listener(pause_listener)
            monitor.release()
            self._monitor = None
            self._pause_listener = None
        forksafe.unregister(self._reset_after_fork)
        installed: Optional[Callable[..., int]] = getattr(self, "_installed_collect", None)
        if installed is not None and gc.collect is installed:
            gc.collect = self._orig_collect
            self._installed_collect = None
        LOG.debug("GCCollector stopped")

    def _reset_after_fork(self) -> None:
        with self._lock:
            self._clear_window()

    def _clear_window(self) -> None:
        # Caller holds _lock. In-place zeros so a reentrant GC callback cannot
        # pair a new stop with a stale start from before the reset.
        for i in range(_GEN_COUNT):
            self._pause_count[i] = 0
            self._pause_total_ns[i] = 0
        self._event_count = 0
        self._explicit_count = 0

    def _patched_collect(self, generation: int = 2) -> int:
        # When stop cannot unwrap because a later wrapper sits on top, keep the
        # old patch in the chain as a pass-through. Restart re-activates counting
        # without wrapping again (that would cycle through the old bound method).
        if self._patch_active:
            # Skip the counter if snapshot() already holds the lock. Waiting
            # would deadlock when a signal handler or debugger calls
            # gc.collect on this same thread. Always forward to the original.
            if self._lock.acquire(False):
                try:
                    self._explicit_count += 1
                finally:
                    self._lock.release()
        return self._orig_collect(generation)

    def _on_pause(self, gen: int, pause_ns: int) -> None:
        # Do not allocate: object creation in a GC callback can recurse.
        if not 0 <= gen < _GEN_COUNT:
            return
        if pause_ns < 0:
            return
        end_ns: int = time.monotonic_ns()
        thread_id: int = _thread.get_ident()
        # Skip when snapshot() already holds the lock. Waiting would block
        # forever if GC re-entered on this same thread (same as
        # GCPauseMonitor._on_gc).
        if not self._lock.acquire(False):
            return
        try:
            if pause_ns >= self._event_threshold_ns and self._event_count < _MAX_PAUSE_EVENTS:
                i: int = self._event_count
                self._event_gen[i] = gen
                self._event_pause_ns[i] = pause_ns
                self._event_end_ns[i] = end_ns
                self._event_thread_id[i] = thread_id
                self._event_count = i + 1
            else:
                self._pause_count[gen] += 1
                self._pause_total_ns[gen] += pause_ns
        finally:
            self._lock.release()

    def snapshot(self) -> None:  # type: ignore[override]
        # Copy primitives, then clear, then allocate. A reentrant GC callback
        # during SampleHandle construction must land in the next window.
        # Skip this interval if a pause listener or patched collect already
        # holds the lock. Waiting would hang shutdown when a signal interrupts
        # the main thread mid-_on_pause / _patched_collect.
        if not self._lock.acquire(False):
            return
        try:
            c0: int = self._pause_count[0]
            c1: int = self._pause_count[1]
            c2: int = self._pause_count[2]
            t0: int = self._pause_total_ns[0]
            t1: int = self._pause_total_ns[1]
            t2: int = self._pause_total_ns[2]
            event_count: int = self._event_count
            for i in range(event_count):
                self._snap_event_gen[i] = self._event_gen[i]
                self._snap_event_pause_ns[i] = self._event_pause_ns[i]
                self._snap_event_end_ns[i] = self._event_end_ns[i]
                self._snap_event_thread_id[i] = self._event_thread_id[i]
            explicit: int = self._explicit_count
            self._pause_count[0] = 0
            self._pause_count[1] = 0
            self._pause_count[2] = 0
            self._pause_total_ns[0] = 0
            self._pause_total_ns[1] = 0
            self._pause_total_ns[2] = 0
            self._event_count = 0
            self._explicit_count = 0
        finally:
            self._lock.release()

        for i in range(event_count):
            gen: int = self._snap_event_gen[i]
            thread_id: int = self._snap_event_thread_id[i]
            event_handle: ddup.SampleHandle = ddup.SampleHandle()
            event_handle.push_gctime(self._snap_event_pause_ns[i], 1)
            event_handle.push_frame(_GEN_NAMES[gen], _GC_FILE, 0, gen)
            event_handle.push_gc_generation(gen)
            event_handle.push_gc_event(_GC_EVENT_PAUSE)
            event_handle.push_monotonic_ns(self._snap_event_end_ns[i])
            # push_threadinfo drops all three labels when the name is empty.
            thread_name: str = _threading.get_thread_name(thread_id) or str(thread_id)
            native_id: int = _threading.get_thread_native_id(thread_id)
            event_handle.push_threadinfo(thread_id, native_id, thread_name)
            event_handle.flush_sample()

        pause_count: tuple[int, int, int] = (c0, c1, c2)
        pause_total_ns: tuple[int, int, int] = (t0, t1, t2)
        for gen in range(_GEN_COUNT):
            n: int = pause_count[gen]
            if n == 0:
                continue
            pause_handle: ddup.SampleHandle = ddup.SampleHandle()
            # pause_total_ns is already the window sum; push_gctime multiplies
            # ns*count (same contract as push_walltime), so pass count=1 for
            # the duration, then add the remaining pause counts with zero
            # duration so gc-samples matches n.
            pause_handle.push_gctime(pause_total_ns[gen], 1)
            if n > 1:
                pause_handle.push_gctime(0, n - 1)
            pause_handle.push_frame(_GEN_NAMES[gen], _GC_FILE, 0, gen)
            pause_handle.push_monotonic_ns(time.monotonic_ns())
            pause_handle.flush_sample()

        config_handle: ddup.SampleHandle = ddup.SampleHandle()
        # Native GC samples carry both gc-time and gc-samples. An explicit
        # gc.collect() has no pause duration of its own here, so add only its
        # event count to gc-samples.
        config_handle.push_gctime(0, explicit)
        config_handle.push_frame(_GC_CONFIG_FRAME, _GC_FILE, 0, 0)
        config_handle.push_monotonic_ns(time.monotonic_ns())
        config_handle.flush_sample()

        if LOG.isEnabledFor(logging.DEBUG):
            thresholds: tuple[int, int, int] = gc.get_threshold()
            enabled: bool = gc.isenabled()
            freeze_count: int = gc.get_freeze_count() if hasattr(gc, "get_freeze_count") else 0
            stats: list[dict[str, int]] = gc.get_stats()
            total_collections: int = sum(int(s.get("collections", 0)) for s in stats)
            LOG.debug(
                "GCCollector snapshot: enabled=%s thresholds=%s freeze=%d total_collections=%d explicit_collect=%d",
                enabled,
                thresholds,
                freeze_count,
                total_collections,
                explicit,
            )
