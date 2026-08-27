from __future__ import annotations

import gc
import os
from pathlib import Path
import threading
from typing import TYPE_CHECKING

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import gc as gc_collector
from tests.profiling.collector import pprof_utils


if TYPE_CHECKING:
    from tests.profiling.collector import pprof_pb2

# libdatadog v41 has no dedicated gc-time variant. These experimental slots
# map to "gc-time" / "gc-samples" once that bump lands.
GC_TIME_TYPE = "experimental-nanoseconds"
GC_SAMPLES_TYPE = "experimental-count"


def _setup_profiler(tmp_path: Path, test_name: str) -> str:
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())
    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()
    return output_filename


def test_gc_config_defaults() -> None:
    from ddtrace.internal.settings.profiling import ProfilingConfig

    config = ProfilingConfig()
    assert config.gc.enabled is False
    assert config.gc.pause_threshold_ns == 1_000_000


def test_gc_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    from ddtrace.internal.settings.profiling import ProfilingConfig

    monkeypatch.setenv("DD_PROFILING_GC_ENABLED", "true")
    monkeypatch.setenv("DD_PROFILING_GC_PAUSE_THRESHOLD_NS", "5000")
    config = ProfilingConfig()
    assert config.gc.enabled is True
    assert config.gc.pause_threshold_ns == 5000


def test_gc_config_str_token(monkeypatch: pytest.MonkeyPatch) -> None:
    from ddtrace.internal.settings.profiling import ProfilingConfig
    from ddtrace.internal.settings.profiling import config_str

    monkeypatch.delenv("DD_PROFILING_GC_ENABLED", raising=False)
    assert "gc" not in config_str(ProfilingConfig()).split("_")

    monkeypatch.setenv("DD_PROFILING_GC_ENABLED", "true")
    assert "gc" in config_str(ProfilingConfig()).split("_")


def test_gc_collect_emits_individual_samples(tmp_path: Path) -> None:
    output_filename = _setup_profiler(tmp_path, "test_gc_individual")

    with gc_collector.GCCollector(pause_threshold_ns=0):
        gc.collect()

    ddup.upload()

    profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, GC_TIME_TYPE)
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=threading.get_ident(),
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="Garbage collection",
                    filename="<runtime>",
                    line_no=0,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )

    time_idx = pprof_utils.get_sample_type_index(profile, GC_TIME_TYPE)
    count_idx = pprof_utils.get_sample_type_index(profile, GC_SAMPLES_TYPE)
    total_ns = sum(s.value[time_idx] for s in samples)
    total_count = sum(s.value[count_idx] for s in samples)
    assert total_ns > 0
    assert total_count >= 1


def test_gc_aggregate_on_snapshot(tmp_path: Path) -> None:
    output_filename = _setup_profiler(tmp_path, "test_gc_aggregate")

    collector = gc_collector.GCCollector(pause_threshold_ns=10**18)
    with collector:
        gc.collect()
        collector.snapshot()

    ddup.upload()

    profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, GC_TIME_TYPE)
    assert len(samples) > 0

    named = [s for s in samples if pprof_utils._sample_has_function_name(profile, s, "Garbage collection")]
    assert named

    time_idx = pprof_utils.get_sample_type_index(profile, GC_TIME_TYPE)
    count_idx = pprof_utils.get_sample_type_index(profile, GC_SAMPLES_TYPE)
    total_ns = sum(s.value[time_idx] for s in named)
    total_count = sum(s.value[count_idx] for s in named)
    assert total_ns > 0
    assert total_count >= 1
