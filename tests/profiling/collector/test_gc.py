"""Tests for the GC observability collector."""

from __future__ import annotations

import _thread
import gc
import threading
from typing import Callable
from unittest import mock

import pytest

from ddtrace.internal import forksafe
from ddtrace.internal.runtime.gc_monitor import GCPauseMonitor
from ddtrace.internal.runtime.gc_monitor import PauseListener
from ddtrace.internal.runtime.gc_monitor import gc_pause_monitor
from ddtrace.internal.settings.profiling import ProfilingConfig
import ddtrace.profiling.collector.gc as gc_collector
from ddtrace.profiling.collector.gc import GCCollector


def test_start_installs_and_stop_removes_hooks() -> None:
    monitor: GCPauseMonitor = gc_pause_monitor()
    orig: Callable[..., int] = gc.collect
    col: GCCollector = GCCollector()
    assert monitor._gc_hook not in gc.callbacks
    col.start()
    listener: PauseListener | None = col._pause_listener
    assert listener is not None
    assert listener in monitor._listeners
    # The collector uses the monitor's gc.callbacks entry, shared with runtime metrics, and adds none of its own.
    assert gc.callbacks.count(monitor._gc_hook) == 1
    assert not any(isinstance(getattr(cb, "__self__", None), GCCollector) for cb in gc.callbacks)
    assert forksafe._registry.count(col._reset_after_fork) == 1
    assert gc.collect is not orig
    col.stop()
    assert monitor._gc_hook not in gc.callbacks
    assert listener not in monitor._listeners
    assert col._reset_after_fork not in forksafe._registry
    assert gc.collect is orig
    # Restart must not leave a duplicate listener behind.
    col.start()
    try:
        assert monitor._listeners.count(col._pause_listener) == 1
    finally:
        col.stop()
    assert col._pause_listener is None
    assert listener not in monitor._listeners


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


def test_explicit_collects_counted_on_config_sample() -> None:
    col: GCCollector = GCCollector()
    col.start()
    try:
        gc.collect()
        gc.collect(0)
        assert col._explicit_count == 2
        # The gc.config sample comes last and carries the window's explicit calls in gc-samples.
        config_handle: mock.MagicMock = _snapshot_handles(col)[-1]
        config_handle.push_frame.assert_called_once_with("gc.config", "gc", 0, 0)
        config_handle.push_gctime.assert_called_once_with(0, 2)
        config_handle.flush_sample.assert_called_once()
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
    pause_handle.push_gctime.assert_called_once_with(1000, 1)
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


def test_lock_held_skips_instead_of_waiting() -> None:
    """With the lock held, as on same-thread reentry, each entry point skips its bookkeeping instead of waiting."""
    col: GCCollector = _make_isolated_collector()
    col._patch_active = True
    col._orig_collect = gc.collect
    col._pause_count[0] = 1
    col._pause_total_ns[0] = 1000
    assert col._lock.acquire(False)
    try:
        col._on_pause(0, 500)
        # Skipping the count must not skip the collection.
        collected: int = col._patched_collect()
        assert isinstance(collected, int)
        with mock.patch.object(gc_collector, "ddup") as mock_ddup:
            col.snapshot()
            mock_ddup.SampleHandle.assert_not_called()
        assert col._pause_count == [1, 0, 0]
        assert col._pause_total_ns == [1000, 0, 0]
        assert col._explicit_count == 0
    finally:
        col._lock.release()


def test_patch_active_set_before_collect_replacement() -> None:
    """_patch_active must be True before gc.collect is published on first start."""
    orig: Callable[..., int] = gc.collect
    col: GCCollector = GCCollector()
    active_at_publish: list[bool] = []

    class _CollectModule:
        """Proxy so assigning gc.collect records _patch_active at publish time."""

        def __getattr__(self, name: str) -> object:
            return getattr(gc, name)

        def __setattr__(self, name: str, value: object) -> None:
            if name == "collect":
                active_at_publish.append(getattr(col, "_patch_active", False))
            setattr(gc, name, value)

    try:
        with mock.patch.object(gc_collector, "gc", _CollectModule()):
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
    col._pause_count[2] = pause_count
    col._pause_total_ns[2] = total_ns

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


def test_overlapping_snapshots_emit_each_pause_once() -> None:
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    col._on_pause(2, 7_000_000)
    handles: list[mock.MagicMock] = []

    def make_handle() -> mock.MagicMock:
        handle: mock.MagicMock = mock.MagicMock()
        handles.append(handle)
        if len(handles) == 1:
            # A second flush runs while the first is still emitting its samples.
            col._on_pause(1, 3_000_000)
            col.snapshot()
        return handle

    with mock.patch.object(gc_collector, "ddup") as mock_ddup:
        mock_ddup.SampleHandle.side_effect = make_handle
        col.snapshot()

    events: list[mock.MagicMock] = [h for h in handles if h.push_gc_event.called]
    emitted: list[tuple[int, int]] = sorted(
        (h.push_gc_generation.call_args[0][0], h.push_gctime.call_args[0][0]) for h in events
    )
    assert emitted == [(1, 3_000_000), (2, 7_000_000)]


def test_pause_event_thread_id_ignores_later_get_ident_patch() -> None:
    col: GCCollector = _make_isolated_collector(pause_event_threshold_ms=1.0)
    os_thread_id: int = threading.get_ident()
    # gevent's monkey patching can replace _thread.get_ident with greenlet ids after ddtrace is imported.
    with mock.patch.object(_thread, "get_ident", return_value=os_thread_id + 1):
        col._on_pause(2, 5_000_000)
    assert col._event_thread_id[0] == os_thread_id


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
    mock_get_ident: mock.MagicMock = mock.MagicMock(return_value=456)
    mock_thread: mock.MagicMock = mock.MagicMock()
    mock_thread.get_native_id.return_value = 789
    with (
        mock.patch.object(gc_collector, "time", mock_time),
        mock.patch.object(gc_collector, "_get_ident", mock_get_ident),
        mock.patch.object(gc_collector, "_thread", mock_thread),
    ):
        # Below the event threshold, then past the event cap.
        col._on_pause(0, 500_000)
        col._event_count = gc_collector._MAX_PAUSE_EVENTS
        col._on_pause(2, 20_000_000)
        assert col._pause_count == [1, 0, 1]
        mock_time.monotonic_ns.assert_not_called()
        mock_get_ident.assert_not_called()
        mock_thread.get_native_id.assert_not_called()

        col._event_count = 0
        col._on_pause(2, 20_000_000)
    assert (col._event_end_ns[0], col._event_thread_id[0], col._event_native_id[0]) == (123, 456, 789)


# ddup's sample-type mask is process-wide and frozen on the first start(), so an
# in-process run after other profiling tests never sees gc-time.
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
    env=dict(DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_pause_labels_in_profile"),
    err=None,
)
def test_gc_pause_labels_in_profile() -> None:
    import os
    import time
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
        service="test_gc_pause_labels_in_profile",
        version="1.0",
        output_filename=pprof_prefix,
        timeline_enabled=True,
    )
    ddup.config_sample_type(ddup.SAMPLE_TYPE_GC)
    ddup.start()

    pause_ns: int = 20_000_000
    slack_ns: int = 20_000_000
    col: GCCollector = GCCollector()
    before_ns: int = time.time_ns()
    col.start()
    try:
        # One generation 2 pause, then generation 0 pauses to five past the cap. Real
        # collections during the test can only add drops on top of those five.
        col._on_pause(2, pause_ns)
        for _ in range(gc_collector._MAX_PAUSE_EVENTS + 4):
            col._on_pause(0, pause_ns)
        after_ns: int = time.time_ns()
        # Keep the flush well after the pauses, so a sample stamped at flush time falls outside them.
        time.sleep(0.1)
        col.snapshot()
    finally:
        col.stop()

    ddup.upload()

    profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(output_filename)
    event_generations: set[int] = set()
    for sample in profile.sample:
        event: Optional[pprof_pb2.Label] = pprof_utils.get_label_with_key(profile.string_table, sample, "gc event")
        if event is None:
            continue
        assert profile.string_table[event.str] == "pause", sample
        generation: Optional[pprof_pb2.Label] = pprof_utils.get_label_with_key(
            profile.string_table, sample, "gc generation"
        )
        assert generation is not None, sample
        event_generations.add(generation.num)
        end: Optional[pprof_pb2.Label] = pprof_utils.get_label_with_key(
            profile.string_table, sample, "end_timestamp_ns"
        )
        assert end is not None, sample
        assert before_ns - slack_ns <= end.num <= after_ns + slack_ns, (before_ns, end.num, after_ns)
    # A zero numeric label must still be exported.
    assert {0, 2} <= event_generations, event_generations

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


def test_rollback_after_failed_first_start_restores_gc_collect() -> None:
    """A first start that fails after wrapping gc.collect must still unwrap it on rollback."""
    orig: Callable[..., int] = gc.collect
    col: GCCollector = GCCollector()
    try:
        with mock.patch.object(gc_collector, "gc_pause_monitor", side_effect=RuntimeError("no monitor")):
            with pytest.raises(RuntimeError):
                col.start()
        col._rollback_start()
        assert gc.collect is orig
    finally:
        gc.collect = orig


@pytest.mark.parametrize("failing_call", ("add_listener", "acquire"))
def test_rollback_after_failed_start_keeps_other_monitor_reference(failing_call: str) -> None:
    """Rollback releases only a monitor reference this start took, so another user (runtime metrics) keeps its own."""
    orig: Callable[..., int] = gc.collect
    monitor: GCPauseMonitor = gc_pause_monitor()
    monitor.acquire()
    col: GCCollector = GCCollector()
    try:
        with mock.patch.object(GCPauseMonitor, failing_call, side_effect=RuntimeError("monitor failed")):
            with pytest.raises(RuntimeError):
                col.start()
        col._rollback_start()
        assert monitor._refcount == 1
        assert monitor._gc_hook in gc.callbacks
        assert gc.collect is orig
    finally:
        monitor.release()
        gc.collect = orig


def test_patched_collect_forwards_the_caller_arguments() -> None:
    """The wrapper passes on exactly what the caller gave, so an earlier gc.collect wrapper keeps working."""
    orig: Callable[..., int] = gc.collect
    seen: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def app_collect(*args: object, **kwargs: object) -> int:
        seen.append((args, kwargs))
        return orig(*args, **kwargs)

    gc.collect = app_collect
    col: GCCollector = GCCollector()
    col.start()
    try:
        gc.collect()
        gc.collect(generation=0)
        assert seen == [((), {}), ((), {"generation": 0})]
        assert col._explicit_count == 2
    finally:
        col.stop()
        gc.collect = orig


@pytest.mark.subprocess(err=None)
def test_real_fork_resets_gc_state_in_the_child_only() -> None:
    # Fork for real while another thread holds the collector's and the monitor's locks. The child must reset both,
    # keep counting collections, and not hang on those locks; the parent keeps its own counts.
    import gc
    import os
    import signal
    import threading
    import time
    import traceback

    from ddtrace.internal.runtime import gc_monitor
    from ddtrace.profiling.collector.gc import GCCollector

    # Only explicit collections, so both sides of the fork can compare exact counts.
    gc.disable()
    col: GCCollector = GCCollector()
    col.start()
    monitor: gc_monitor.GCPauseMonitor = gc_monitor.gc_pause_monitor()
    gc.collect()
    gc.collect(0)
    # Stands in for a collection another thread had started when the process forked.
    monitor._start_ns[2] = 1
    parent_collector: tuple[list[int], list[int], int] = (
        list(col._pause_count),
        list(col._pause_total_ns),
        col._explicit_count,
    )
    parent_monitor: tuple[int, int, int] = (monitor._count, monitor._total_ns, monitor._max_ns)
    assert parent_collector[2] == 2
    assert sum(parent_collector[0]) == parent_monitor[0] >= 2

    locks_held: threading.Event = threading.Event()
    release_locks: threading.Event = threading.Event()

    def hold_locks() -> None:
        with col._lock, monitor._lock:
            locks_held.set()
            release_locks.wait()

    holder: threading.Thread = threading.Thread(target=hold_locks)
    holder.start()
    assert locks_held.wait(10)

    pid: int = os.fork()
    if pid == 0:
        exit_code: int = 1
        try:
            assert col._pause_count == [0, 0, 0]
            assert col._pause_total_ns == [0, 0, 0]
            assert col._explicit_count == 0
            assert monitor._start_ns == [0, 0, 0]
            assert (monitor._count, monitor._total_ns, monitor._max_ns) == (0, 0, 0)
            gc.collect(0)
            assert col._explicit_count == 1
            assert sum(col._pause_count) == monitor._count >= 1
            assert sum(col._pause_total_ns) == monitor._total_ns >= monitor._max_ns
            exit_code = 0
        except BaseException:
            os.write(2, traceback.format_exc().encode())
        finally:
            os._exit(exit_code)

    release_locks.set()
    holder.join()
    status: int = -1
    for _ in range(600):
        waited: tuple[int, int] = os.waitpid(pid, os.WNOHANG)
        if waited[0] == pid:
            status = waited[1]
            break
        time.sleep(0.05)
    else:
        os.kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
        raise AssertionError("the forked child hung, so a fork hook waited on a lock held at fork time")
    assert os.WIFEXITED(status) and os.WEXITSTATUS(status) == 0, status

    try:
        assert (list(col._pause_count), list(col._pause_total_ns), col._explicit_count) == parent_collector
        assert (monitor._count, monitor._total_ns, monitor._max_ns) == parent_monitor
        assert monitor._start_ns[2] == 1
        gc.collect(0)
        assert col._explicit_count == 3
    finally:
        col.stop()
