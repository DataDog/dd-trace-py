"""Tests for the GC observability collector."""

from __future__ import annotations

import gc
from typing import Callable
from unittest import mock

import pytest

from ddtrace.internal import forksafe
from ddtrace.internal.runtime.gc_monitor import GCPauseMonitor
from ddtrace.internal.runtime.gc_monitor import PauseListener
from ddtrace.internal.runtime.gc_monitor import gc_pause_monitor
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
        assert col._explicit_count == 1
        col._reset_after_fork()
        assert col._explicit_count == 0
        assert col._pause_count == [0, 0, 0]
        assert col._pause_total_ns == [0, 0, 0]
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


def _make_isolated_collector() -> GCCollector:
    """Create a GCCollector with internal state initialized but NOT registered
    in gc.callbacks.  Use for unit tests that call _on_pause directly to avoid
    interference from real background GC events.
    """
    col = GCCollector()
    col._lock = forksafe.Lock()
    col._pause_count = [0, 0, 0]
    col._pause_total_ns = [0, 0, 0]
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


def test_on_pause_records_pause_gctime() -> None:
    col: GCCollector = _make_isolated_collector()
    with mock.patch.object(gc_collector, "ddup") as mock_ddup:
        col._on_pause(0, 1000)
        mock_ddup.SampleHandle.assert_not_called()
    handles: list[mock.MagicMock] = _snapshot_handles(col)

    assert len(handles) == 2
    pause_handle: mock.MagicMock = handles[0]
    pause_handle.push_gctime.assert_called_once_with(1000, 1)
    pause_handle.push_walltime.assert_not_called()
    pause_handle.push_frame.assert_called_once_with("gc.collect[gen=0]", "gc", 0, 0)
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
    pause_handle.push_frame.assert_called_once_with("gc.collect[gen=2]", "gc", 0, 2)
    pause_handle.push_walltime.assert_not_called()


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
