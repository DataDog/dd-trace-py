"""Tests for the GC observability collector."""

from __future__ import annotations

import gc
import threading
from typing import Callable
from unittest import mock

import pytest

from ddtrace.internal import forksafe
from ddtrace.internal.settings.profiling import ProfilingConfig
import ddtrace.profiling.collector.gc as gc_collector
from ddtrace.profiling.collector.gc import GCCollector


# ---------------------------------------------------------------------------
# Unit tests — no ddup required
# ---------------------------------------------------------------------------


def test_gc_callbacks_registered() -> None:
    from ddtrace.internal.runtime.gc_monitor import gc_pause_monitor

    monitor = gc_pause_monitor()
    col = GCCollector()
    assert monitor._gc_hook not in gc.callbacks
    col.start()
    assert monitor._gc_hook in gc.callbacks
    col.stop()
    assert monitor._gc_hook not in gc.callbacks


def test_gc_collect_not_patched_after_stop() -> None:
    orig = gc.collect
    col = GCCollector()
    col.start()
    assert gc.collect is not orig
    col.stop()
    assert gc.collect is orig


def test_stop_preserves_later_gc_collect_wrapper() -> None:
    orig: Callable[..., int] = gc.collect
    col: GCCollector = GCCollector()
    col.start()
    patched: Callable[..., int] = gc.collect

    def later_wrapper(generation: int = 2) -> int:
        return patched(generation)

    gc.collect = later_wrapper
    try:
        col.stop()
        assert gc.collect is later_wrapper
        # Restart must not re-wrap the later wrapper (that cycles through the
        # old bound _patched_collect and raises RecursionError on collect).
        col.start()
        try:
            collected: int = gc.collect()
            assert isinstance(collected, int)
            assert col._explicit_count == 1
        finally:
            col.stop()
        assert gc.collect is later_wrapper
    finally:
        gc.collect = orig


def test_stop_removes_pause_listener() -> None:
    from ddtrace.internal.runtime.gc_monitor import GCPauseMonitor
    from ddtrace.internal.runtime.gc_monitor import PauseListener
    from ddtrace.internal.runtime.gc_monitor import gc_pause_monitor

    monitor: GCPauseMonitor = gc_pause_monitor()
    col: GCCollector = GCCollector()
    col.start()
    listener: PauseListener | None = col._pause_listener
    assert listener is not None
    assert listener in monitor._listeners
    col.stop()
    assert listener not in monitor._listeners
    # Restart must not leave a duplicate listener behind.
    col.start()
    try:
        assert monitor._listeners.count(col._pause_listener) == 1
    finally:
        col.stop()
    assert col._pause_listener is None
    assert all(item is not listener for item in monitor._listeners)


def test_reset_after_fork_clears_in_flight_state() -> None:
    col: GCCollector = GCCollector()
    col.start()
    try:
        gc.collect()
        col._pause_count[0] = 5
        col._event_count = 3
        col._dropped_pause_count = 2
        col._dropped_pause_ns = 30_000_000
        assert col._explicit_count == 1
        col._reset_after_fork()
        assert col._explicit_count == 0
        assert col._pause_count == [0, 0, 0]
        assert col._pause_total_ns == [0, 0, 0]
        assert col._event_count == 0
        assert col._dropped_pause_count == 0
        assert col._dropped_pause_ns == 0
    finally:
        col.stop()


def test_fork_hook_registered_and_unregistered() -> None:
    with mock.patch.object(gc_collector, "forksafe") as mock_forksafe:
        col: GCCollector = GCCollector()
        col.start()
        mock_forksafe.register.assert_called_once_with(col._reset_after_fork)
        col.stop()
        mock_forksafe.unregister.assert_called_once_with(col._reset_after_fork)


def test_explicit_count_increments() -> None:
    col = GCCollector()
    col.start()
    try:
        assert col._explicit_count == 0
        gc.collect()
        assert col._explicit_count == 1
        gc.collect(0)
        assert col._explicit_count == 2
    finally:
        col.stop()


def test_explicit_count_resets_on_snapshot() -> None:
    col = GCCollector()
    col.start()
    try:
        gc.collect()
        gc.collect()
        assert col._explicit_count == 2
        with mock.patch.object(gc_collector, "ddup") as mock_ddup:
            mock_handle = mock.MagicMock()
            mock_ddup.SampleHandle.return_value = mock_handle
            col.snapshot()
        assert col._explicit_count == 0
    finally:
        col.stop()


def _make_isolated_collector(pause_event_threshold_ms: float = 1.0) -> GCCollector:
    """Create a GCCollector with internal state initialized but NOT registered
    in gc.callbacks.  Use for unit tests that call _on_pause directly to avoid
    interference from real background GC events.
    """
    col: GCCollector = GCCollector(pause_event_threshold_ms=pause_event_threshold_ms)
    col._lock = forksafe.Lock()
    col._pause_count = [0, 0, 0]
    col._pause_total_ns = [0, 0, 0]
    col._event_gen = [0] * gc_collector._MAX_PAUSE_EVENTS
    col._event_pause_ns = [0] * gc_collector._MAX_PAUSE_EVENTS
    col._event_end_ns = [0] * gc_collector._MAX_PAUSE_EVENTS
    col._event_thread_id = [0] * gc_collector._MAX_PAUSE_EVENTS
    col._event_native_id = [0] * gc_collector._MAX_PAUSE_EVENTS
    col._event_count = 0
    col._snap_event_gen = [0] * gc_collector._MAX_PAUSE_EVENTS
    col._snap_event_pause_ns = [0] * gc_collector._MAX_PAUSE_EVENTS
    col._snap_event_end_ns = [0] * gc_collector._MAX_PAUSE_EVENTS
    col._snap_event_thread_id = [0] * gc_collector._MAX_PAUSE_EVENTS
    col._snap_event_native_id = [0] * gc_collector._MAX_PAUSE_EVENTS
    col._dropped_pause_count = 0
    col._dropped_pause_ns = 0
    col._explicit_count = 0
    return col


def _snapshot_handles(col: GCCollector) -> list[mock.MagicMock]:
    handles: list[mock.MagicMock] = []

    def make_handle() -> mock.MagicMock:
        h: mock.MagicMock = mock.MagicMock()
        handles.append(h)
        return h

    with mock.patch.object(gc_collector, "ddup") as mock_ddup:
        mock_ddup.SampleHandle.side_effect = make_handle
        col.snapshot()
    return handles


def _pause_handles(handles: list[mock.MagicMock]) -> list[mock.MagicMock]:
    return [h for h in handles if h.push_frame.call_args[0][0] != "gc.config"]


def _config_handle(handles: list[mock.MagicMock]) -> mock.MagicMock:
    config_handles: list[mock.MagicMock] = [h for h in handles if h.push_frame.call_args[0][0] == "gc.config"]
    assert len(config_handles) == 1
    return config_handles[0]


def test_on_pause_records_pause_gctime() -> None:
    # Sub-threshold pauses stay in the per-generation window totals.
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    col._on_pause(0, 1000)
    handles: list[mock.MagicMock] = _snapshot_handles(col)

    assert len(handles) == 2
    pause_handle: mock.MagicMock = handles[0]
    pause_handle.push_gctime.assert_called_once()
    args: tuple[int, int] = pause_handle.push_gctime.call_args[0]
    pause_ns: int
    count: int
    pause_ns, count = args
    assert pause_ns >= 0
    assert count == 1
    pause_handle.push_walltime.assert_not_called()
    pause_handle.push_frame.assert_called_once_with("gc.collect[gen=0]", "gc", 0, 0)
    pause_handle.push_gc_event.assert_not_called()
    pause_handle.push_threadinfo.assert_not_called()
    pause_handle.flush_sample.assert_called_once()
    pause_handle.push_alloc.assert_not_called()
    config_handle: mock.MagicMock = handles[1]
    config_handle.push_gctime.assert_called_once_with(0, 0)
    config_handle.push_walltime.assert_not_called()
    config_handle.push_frame.assert_called_once_with("gc.config", "gc", 0, 0)


def test_on_pause_skips_when_lock_held() -> None:
    """Reentrant pause while snapshot holds the lock must not block forever."""
    col: GCCollector = _make_isolated_collector()
    assert col._lock.acquire(False)
    try:
        col._on_pause(0, 1000)
        assert col._pause_count == [0, 0, 0]
        assert col._pause_total_ns == [0, 0, 0]
    finally:
        col._lock.release()


def test_patched_collect_skips_counter_when_lock_held() -> None:
    """Reentrant gc.collect while snapshot holds the lock must still collect."""
    col: GCCollector = _make_isolated_collector()
    col._patch_active = True
    col._orig_collect = gc.collect
    assert col._lock.acquire(False)
    try:
        collected: int = col._patched_collect()
        assert isinstance(collected, int)
        assert col._explicit_count == 0
    finally:
        col._lock.release()


def test_snapshot_skips_when_lock_held() -> None:
    """Snapshot must not wait if a pause listener already holds the lock."""
    col: GCCollector = _make_isolated_collector()
    col._pause_count[0] = 1
    col._pause_total_ns[0] = 1000
    assert col._lock.acquire(False)
    try:
        with mock.patch.object(gc_collector, "ddup") as mock_ddup:
            col.snapshot()
            mock_ddup.SampleHandle.assert_not_called()
        assert col._pause_count[0] == 1
        assert col._pause_total_ns[0] == 1000
    finally:
        col._lock.release()


def test_patch_active_set_before_collect_replacement() -> None:
    """_patch_active must be True before gc.collect is published on first start."""
    import inspect

    src: str = inspect.getsource(GCCollector._start_service)
    active_pos: int = src.index("self._patch_active = True")
    publish_pos: int = src.index("gc.collect = self._installed_collect")
    assert active_pos < publish_pos

    orig: Callable[..., int] = gc.collect
    col: GCCollector = GCCollector()
    active_at_publish: list[bool] = []

    class _CollectModule:
        """Proxy so assigning gc.collect records _patch_active at publish time."""

        def __init__(self, real_gc: object) -> None:
            self._real_gc: object = real_gc

        def __getattr__(self, name: str) -> object:
            return getattr(self._real_gc, name)

        def __setattr__(self, name: str, value: object) -> None:
            if name.startswith("_"):
                object.__setattr__(self, name, value)
                return
            if name == "collect":
                active_at_publish.append(getattr(col, "_patch_active", False))
            setattr(self._real_gc, name, value)

    try:
        with mock.patch.object(gc_collector, "gc", _CollectModule(gc)):
            col.start()
        assert active_at_publish == [True]
        assert col._patch_active is True
        assert gc.collect is col._installed_collect
    finally:
        col.stop()
        gc.collect = orig


def test_snapshot_gctime_equals_total_pause_ns() -> None:
    """Emitted gc-time must be the window sum, and gc-samples the pause count."""
    col: GCCollector = _make_isolated_collector()
    total_ns: int = 1_000_000
    pause_count: int = 5
    col._pause_count[0] = pause_count
    col._pause_total_ns[0] = total_ns

    handles: list[mock.MagicMock] = _snapshot_handles(col)

    pause_handle: mock.MagicMock = handles[0]
    gctime_calls: list[tuple[int, int]] = [c.args for c in pause_handle.push_gctime.call_args_list]
    assert gctime_calls == [(total_ns, 1), (0, pause_count - 1)]
    pause_handle.push_walltime.assert_not_called()


def test_long_pause_emitted_as_timestamped_event() -> None:
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    col._on_pause(2, 5_000_000)
    col._on_pause(2, 100)

    pause_handles: list[mock.MagicMock] = _pause_handles(_snapshot_handles(col))

    events: list[mock.MagicMock] = [h for h in pause_handles if h.push_gc_event.called]
    buckets: list[mock.MagicMock] = [h for h in pause_handles if not h.push_gc_event.called]
    assert len(events) == 1
    assert len(buckets) == 1
    events[0].push_gctime.assert_called_once_with(5_000_000, 1)
    events[0].push_gc_generation.assert_called_once_with(2)
    events[0].push_gc_event.assert_called_once_with(gc_collector._GC_EVENT_PAUSE)
    end_ns: int = events[0].push_monotonic_ns.call_args[0][0]
    assert end_ns > 0
    events[0].push_threadinfo.assert_called_once()
    buckets[0].push_gctime.assert_called_once_with(100, 1)
    buckets[0].push_gc_event.assert_not_called()
    buckets[0].push_threadinfo.assert_not_called()
    assert sum(h.push_gctime.call_args_list[0].args[0] for h in pause_handles) == 5_000_100


def test_pause_event_keeps_native_id_after_thread_exits() -> None:
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    worker: threading.Thread = threading.Thread(target=col._on_pause, args=(2, 5_000_000))
    worker.start()
    worker.join()

    events: list[mock.MagicMock] = [h for h in _pause_handles(_snapshot_handles(col)) if h.push_gc_event.called]
    assert len(events) == 1
    threadinfo: tuple[int, int, str] = events[0].push_threadinfo.call_args[0]
    assert threadinfo[:2] == (worker.ident, worker.native_id)


def test_pause_events_past_cap_roll_into_aggregates() -> None:
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    with mock.patch.object(gc_collector, "_MAX_PAUSE_EVENTS", 2):
        # Ring was sized at construction; shrink the live cap only.
        for _ in range(4):
            col._on_pause(0, 2_000_000)

    pause_handles: list[mock.MagicMock] = _pause_handles(_snapshot_handles(col))

    events: list[mock.MagicMock] = [h for h in pause_handles if h.push_gc_event.called]
    buckets: list[mock.MagicMock] = [h for h in pause_handles if not h.push_gc_event.called]
    assert len(events) == 2
    assert len(buckets) == 1
    buckets[0].push_gctime.assert_any_call(4_000_000, 1)
    buckets[0].push_gctime.assert_any_call(0, 1)
    assert sum(h.push_gctime.call_args_list[0].args[0] for h in pause_handles) == 8_000_000


def test_pause_events_capped_at_1024() -> None:
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    long_ns: int = 20_000_000
    for _ in range(1027):
        col._on_pause(2, long_ns)

    assert col._event_count == 1024
    assert col._dropped_pause_count == 3
    assert col._dropped_pause_ns == 3 * long_ns
    assert col._pause_count == [0, 0, 3]
    assert col._pause_total_ns == [0, 0, 3 * long_ns]


def test_last_pause_event_slots_only_take_long_pauses() -> None:
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    short_ns: int = 2_000_000
    for _ in range(960):
        col._on_pause(0, short_ns)
    assert col._event_count == 960

    col._on_pause(0, short_ns)
    assert col._event_count == 960
    col._on_pause(2, 10_000_000)
    assert col._event_count == 961
    assert col._event_gen[960] == 2
    assert col._event_pause_ns[960] == 10_000_000
    assert col._dropped_pause_count == 1
    assert col._dropped_pause_ns == short_ns
    assert col._pause_count == [1, 0, 0]
    assert col._pause_total_ns == [short_ns, 0, 0]


def test_dropped_pauses_counted_on_config_sample_per_window() -> None:
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    for _ in range(960):
        col._on_pause(0, 2_000_000)
    col._on_pause(0, 3_000_000)
    col._on_pause(1, 4_000_000)
    # Below the event threshold: a window total, not a dropped event.
    col._on_pause(0, 500_000)

    handles: list[mock.MagicMock] = _snapshot_handles(col)

    config: mock.MagicMock = _config_handle(handles)
    config.push_gc_dropped_pauses.assert_called_once_with(2)
    config.push_gc_dropped_pause_ns.assert_called_once_with(7_000_000)
    # Kept events plus window totals cover every pause exactly once.
    pause_handles: list[mock.MagicMock] = _pause_handles(handles)
    assert sum(h.push_gctime.call_args_list[0].args[0] for h in pause_handles) == 960 * 2_000_000 + 7_500_000
    assert sum(c.args[1] for h in pause_handles for c in h.push_gctime.call_args_list) == 963

    next_config: mock.MagicMock = _config_handle(_snapshot_handles(col))
    next_config.push_gc_dropped_pauses.assert_not_called()
    next_config.push_gc_dropped_pause_ns.assert_not_called()


def test_pause_event_threshold_from_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DD_PROFILING_GC_PAUSE_EVENT_THRESHOLD_MS", raising=False)
    assert ProfilingConfig().gc.pause_event_threshold_ms == 1.0
    monkeypatch.setenv("DD_PROFILING_GC_PAUSE_EVENT_THRESHOLD_MS", "2.5")
    assert ProfilingConfig().gc.pause_event_threshold_ms == 2.5
    assert GCCollector(pause_event_threshold_ms=2.5)._event_threshold_ns == 2_500_000


def test_on_pause_does_not_grow_event_buffers() -> None:
    """GC callback must write into the preallocated ring, not append/allocate lists."""
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    gen_id: int = id(col._event_gen)
    pause_id: int = id(col._event_pause_ns)
    end_id: int = id(col._event_end_ns)
    thread_id_buf: int = id(col._event_thread_id)
    native_id_buf: int = id(col._event_native_id)
    col._on_pause(1, 2_000_000)
    assert id(col._event_gen) == gen_id
    assert id(col._event_pause_ns) == pause_id
    assert id(col._event_end_ns) == end_id
    assert id(col._event_thread_id) == thread_id_buf
    assert id(col._event_native_id) == native_id_buf
    assert len(col._event_gen) == gc_collector._MAX_PAUSE_EVENTS
    assert col._event_count == 1
    assert col._pause_count == [0, 0, 0]


def test_on_pause_reads_clock_and_thread_ids_only_for_kept_events() -> None:
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    mock_time: mock.MagicMock = mock.MagicMock()
    mock_time.monotonic_ns.return_value = 123
    mock_thread: mock.MagicMock = mock.MagicMock()
    mock_thread.get_ident.return_value = 456
    mock_thread.get_native_id.return_value = 789
    with mock.patch.object(gc_collector, "time", mock_time), mock.patch.object(gc_collector, "_thread", mock_thread):
        # Below the event threshold, then past the event cap.
        col._on_pause(0, 500_000)
        col._event_count = gc_collector._MAX_PAUSE_EVENTS
        col._on_pause(2, 20_000_000)
        assert col._pause_count == [1, 0, 1]
        mock_time.monotonic_ns.assert_not_called()
        mock_thread.get_ident.assert_not_called()
        mock_thread.get_native_id.assert_not_called()

        col._event_count = 0
        col._on_pause(2, 20_000_000)
    assert (col._event_end_ns[0], col._event_thread_id[0], col._event_native_id[0]) == (123, 456, 789)


def test_snapshot_emits_config_sample() -> None:
    # Isolated: starting the collector registers a monitor listener, and a live
    # pause during snapshot() would also call SampleHandle.push_gctime.
    col: GCCollector = _make_isolated_collector()
    col._explicit_count = 2

    with mock.patch.object(gc_collector, "ddup") as mock_ddup:
        mock_handle: mock.MagicMock = mock.MagicMock()
        mock_ddup.SampleHandle.return_value = mock_handle
        col.snapshot()

    mock_handle.push_gctime.assert_called_once_with(0, 2)
    mock_handle.push_walltime.assert_not_called()
    mock_handle.push_frame.assert_called_once_with("gc.config", "gc", 0, 0)
    mock_handle.flush_sample.assert_called_once()


# ---------------------------------------------------------------------------
# Integration test — emits real ddup samples and reads back pprof.
# Fresh process: ddup's sample-type mask is process-wide and is frozen on
# the first start(), so an in-process run after other profiling tests never
# sees gc-time.
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_pause_samples_appear_in_profile"),
    err=None,
)
def test_gc_pause_samples_appear_in_profile() -> None:
    import gc
    import os

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.gc import GCCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.pprof_utils import pprof_pb2

    pprof_prefix: str = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_filename: str = pprof_prefix + "." + str(os.getpid())
    assert ddup.is_available
    ddup.config(
        env="test",
        service="test_gc_pause_samples_appear_in_profile",
        version="1.0",
        output_filename=pprof_prefix,
    )
    ddup.config_sample_type(ddup.SAMPLE_TYPE_GC)
    ddup.start()

    col: GCCollector = GCCollector()
    col.start()
    try:
        # Force a few collections to guarantee at least one gc.callbacks event
        gc.collect(0)
        gc.collect(1)
        gc.collect(2)
        col.snapshot()
    finally:
        col.stop()

    ddup.upload()

    profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(output_filename)
    sample_types: list[str] = [profile.string_table[sample_type.type] for sample_type in profile.sample_type]
    assert "gc-time" in sample_types, sample_types
    gc_time_samples: list[pprof_pb2.Sample] = pprof_utils.get_samples_with_value_type(profile, "gc-time")

    # At least one sample must have a gc.collect frame
    gc_samples: list[pprof_pb2.Sample] = [
        s
        for s in gc_time_samples
        if any(
            "gc.collect" in pprof_utils.get_location_from_id(profile, loc_id).function_name for loc_id in s.location_id
        )
    ]
    assert len(gc_samples) > 0, "Expected at least one gc.collect gc-time sample"
    wall_time_samples: list[pprof_pb2.Sample] = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert not any(
        any(
            "gc.collect" in pprof_utils.get_location_from_id(profile, loc_id).function_name
            for loc_id in sample.location_id
        )
        for sample in wall_time_samples
    )


@pytest.mark.subprocess(
    env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_dropped_pause_labels_in_profile"),
    err=None,
)
def test_gc_dropped_pause_labels_in_profile() -> None:
    import os
    from typing import Optional

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import gc as gc_collector
    from ddtrace.profiling.collector.gc import GCCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.pprof_utils import pprof_pb2

    pprof_prefix: str = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_filename: str = pprof_prefix + "." + str(os.getpid())
    assert ddup.is_available
    ddup.config(
        env="test",
        service="test_gc_dropped_pause_labels_in_profile",
        version="1.0",
        output_filename=pprof_prefix,
    )
    ddup.config_sample_type(ddup.SAMPLE_TYPE_GC)
    ddup.start()

    pause_ns: int = 20_000_000
    col: GCCollector = GCCollector()
    col.start()
    try:
        # Real collections during the test can only add drops on top of the extra five.
        for _ in range(gc_collector._MAX_PAUSE_EVENTS + 5):
            col._on_pause(0, pause_ns)
        col.snapshot()
    finally:
        col.stop()

    ddup.upload()

    profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(output_filename)
    config_samples: list[pprof_pb2.Sample] = [
        s
        for s in profile.sample
        if any(
            pprof_utils.get_location_from_id(profile, loc_id).function_name == "gc.config" for loc_id in s.location_id
        )
    ]
    assert len(config_samples) == 1, config_samples
    dropped: Optional[pprof_pb2.Label] = pprof_utils.get_label_with_key(
        profile.string_table, config_samples[0], "gc dropped pauses"
    )
    dropped_ns: Optional[pprof_pb2.Label] = pprof_utils.get_label_with_key(
        profile.string_table, config_samples[0], "gc dropped pause ns"
    )
    assert dropped is not None and dropped.num >= 5, config_samples[0]
    assert dropped_ns is not None and dropped_ns.num >= 5 * pause_ns, config_samples[0]


def test_gc_collector_default_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DD_PROFILING_GC_ENABLED", raising=False)
    # Fresh instance — do not importlib.reload the settings module. Reload
    # replaces ProfilingConfigHeap with a second class object and breaks
    # isinstance() in _derive_default_heap_sample_size for later tests.
    config: ProfilingConfig = ProfilingConfig()
    assert not config.gc.enabled


def test_gc_collector_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_PROFILING_GC_ENABLED", "false")
    config: ProfilingConfig = ProfilingConfig()
    assert not config.gc.enabled


def test_gc_collector_enabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_PROFILING_GC_ENABLED", "true")
    config: ProfilingConfig = ProfilingConfig()
    assert config.gc.enabled


def test_gc_collector_shares_monitor_callback() -> None:
    from ddtrace.internal.runtime.gc_monitor import gc_pause_monitor

    monitor = gc_pause_monitor()
    col = GCCollector()
    col.start()
    try:
        assert monitor._gc_hook in gc.callbacks
        assert gc.callbacks.count(monitor._gc_hook) == 1
        owners = [getattr(cb, "__self__", None) for cb in gc.callbacks]
        assert not any(type(o).__name__ == "GCCollector" for o in owners)
    finally:
        col.stop()


def test_gc_flag_keeps_profiler_positional_parameter_order() -> None:
    import inspect

    from ddtrace.profiling import profiler

    # Profiler(*args, **kwargs) forwards positionally, so a parameter inserted
    # before existing ones would rebind callers that pass arguments by position.
    existing: list[str] = [
        "service",
        "tags",
        "env",
        "version",
        "tracer",
        "api_key",
        "_memory_collector_enabled",
        "_stack_collector_enabled",
        "_lock_collector_enabled",
        "_pytorch_collector_enabled",
        "_exception_profiling_enabled",
        "enable_code_provenance",
        "endpoint_collection_enabled",
    ]
    params: list[str] = list(inspect.signature(profiler._ProfilerInstance.__init__).parameters)[1:]
    assert params[: len(existing)] == existing
    assert "_gc_collector_enabled" in params[len(existing) :]


@pytest.mark.subprocess()
def test_default_gc_collector_not_installed() -> None:
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import gc as gc_collector

    assert all(not isinstance(col, gc_collector.GCCollector) for col in profiler.Profiler()._profiler._collectors)


@pytest.mark.subprocess(env=dict(DD_PROFILING_GC_ENABLED="true"))
def test_enable_gc_collector_via_env() -> None:
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import gc as gc_collector

    assert any(isinstance(col, gc_collector.GCCollector) for col in profiler.Profiler()._profiler._collectors)


@pytest.mark.subprocess(env=dict(DD_PROFILING_GC_ENABLED="false"))
def test_disable_gc_collector_via_env() -> None:
    from ddtrace.profiling import profiler
    from ddtrace.profiling.collector import gc as gc_collector

    assert all(not isinstance(col, gc_collector.GCCollector) for col in profiler.Profiler()._profiler._collectors)
