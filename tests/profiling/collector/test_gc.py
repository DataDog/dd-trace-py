"""Tests for the GC observability collector."""

from __future__ import annotations

import gc
import os
from pathlib import Path
from unittest import mock

import pytest

from ddtrace.internal.datadog.profiling import ddup
import ddtrace.profiling.collector.gc as _gc_module
from ddtrace.profiling.collector.gc import GCCollector
from tests.profiling.collector import pprof_utils


def _setup_profiler(tmp_path: Path, test_name: str) -> str:
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())
    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()
    return output_filename


# ---------------------------------------------------------------------------
# Unit tests — no ddup required
# ---------------------------------------------------------------------------


def test_gc_callbacks_registered() -> None:
    col = GCCollector()
    assert col._on_gc not in gc.callbacks
    col.start()
    assert col._on_gc in gc.callbacks
    col.stop()
    assert col._on_gc not in gc.callbacks


def test_gc_collect_not_patched_after_stop() -> None:
    orig = gc.collect
    col = GCCollector()
    col.start()
    assert gc.collect is not orig
    col.stop()
    assert gc.collect is orig


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
        with mock.patch.object(_gc_module, "ddup") as mock_ddup:
            mock_handle = mock.MagicMock()
            mock_ddup.SampleHandle.return_value = mock_handle
            col.snapshot()
        assert col._explicit_count == 0
    finally:
        col.stop()


def _make_isolated_collector() -> GCCollector:
    """Create a GCCollector with internal state initialized but NOT registered
    in gc.callbacks.  Use for unit tests that call _on_gc directly to avoid
    interference from real background GC events.
    """
    col = GCCollector()
    col._start_ns = {}
    col._explicit_count = 0
    return col


def test_on_gc_records_pause_walltime() -> None:
    col = _make_isolated_collector()
    handles: list[mock.MagicMock] = []

    def make_handle() -> mock.MagicMock:
        h = mock.MagicMock()
        handles.append(h)
        return h

    with mock.patch.object(_gc_module, "ddup") as mock_ddup:
        mock_ddup.SampleHandle.side_effect = make_handle
        col._on_gc("start", {"generation": 0})
        col._on_gc("stop", {"generation": 0, "collected": 5, "uncollectable": 0})

    assert len(handles) == 2
    pause_handle = handles[0]
    pause_handle.push_walltime.assert_called_once()
    args = pause_handle.push_walltime.call_args[0]
    pause_ns, count = args
    assert pause_ns >= 0
    assert count == 1
    pause_handle.push_frame.assert_called_once_with("gc.collect[gen=0]", "gc", 0, 0)
    pause_handle.flush_sample.assert_called_once()


def test_on_gc_emits_alloc_sample_for_collected_objects() -> None:
    col = _make_isolated_collector()
    handles: list[mock.MagicMock] = []

    def make_handle() -> mock.MagicMock:
        h = mock.MagicMock()
        handles.append(h)
        return h

    with mock.patch.object(_gc_module, "ddup") as mock_ddup:
        mock_ddup.SampleHandle.side_effect = make_handle
        col._on_gc("start", {"generation": 1})
        col._on_gc("stop", {"generation": 1, "collected": 10, "uncollectable": 0})

    # First handle: walltime; second handle: alloc for collected objects
    assert len(handles) == 2
    alloc_handle = handles[1]
    alloc_handle.push_alloc.assert_called_once_with(10, 1)
    alloc_handle.push_frame.assert_called_once_with("gc.collect[gen=1]", "gc", 0, 1)
    alloc_handle.flush_sample.assert_called_once()


def test_on_gc_no_alloc_sample_when_zero_collected() -> None:
    col = _make_isolated_collector()
    handles: list[mock.MagicMock] = []

    def make_handle() -> mock.MagicMock:
        h = mock.MagicMock()
        handles.append(h)
        return h

    with mock.patch.object(_gc_module, "ddup") as mock_ddup:
        mock_ddup.SampleHandle.side_effect = make_handle
        col._on_gc("start", {"generation": 0})
        col._on_gc("stop", {"generation": 0, "collected": 0, "uncollectable": 0})

    assert len(handles) == 1


def test_on_gc_stop_without_start_is_noop() -> None:
    col = _make_isolated_collector()
    with mock.patch.object(_gc_module, "ddup") as mock_ddup:
        col._on_gc("stop", {"generation": 2, "collected": 3, "uncollectable": 0})
        mock_ddup.SampleHandle.assert_not_called()


def test_snapshot_emits_config_sample() -> None:
    col = GCCollector()
    col.start()
    try:
        gc.collect()
        gc.collect()

        with mock.patch.object(_gc_module, "ddup") as mock_ddup:
            mock_handle = mock.MagicMock()
            mock_ddup.SampleHandle.return_value = mock_handle
            col.snapshot()

        mock_handle.push_walltime.assert_called_once_with(0, 2)
        mock_handle.push_frame.assert_called_once_with("gc.config", "gc", 0, 0)
        mock_handle.flush_sample.assert_called_once()
    finally:
        col.stop()


# ---------------------------------------------------------------------------
# Integration test — emits real ddup samples and reads back pprof
# ---------------------------------------------------------------------------


def test_gc_pause_samples_appear_in_profile(tmp_path: Path) -> None:
    output_filename = _setup_profiler(tmp_path, "test_gc_pause_samples_appear_in_profile")

    col = GCCollector()
    col.start()
    try:
        # Force a few collections to guarantee at least one gc.callbacks event
        gc.collect(0)
        gc.collect(1)
        gc.collect(2)
    finally:
        col.stop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    wall_time_samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")

    # At least one sample must have a gc.collect frame
    gc_samples = [
        s
        for s in wall_time_samples
        if any(
            "gc.collect" in pprof_utils.get_location_from_id(profile, loc_id).function_name for loc_id in s.location_id
        )
    ]
    assert len(gc_samples) > 0, "Expected at least one gc.collect wall-time sample"


def test_gc_collector_disabled_by_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DD_PROFILING_GC_ENABLED", "false")
    # Re-import to pick up the env var
    import importlib

    import ddtrace.internal.settings.profiling as prof_settings

    importlib.reload(prof_settings)
    assert not prof_settings.config.gc.enabled
