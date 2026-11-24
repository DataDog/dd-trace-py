"""What the profiling backend reads from GC Time profiles.

The gc-time profile type, its flame graphs and the Timeline GC lane parse these profiles as they
are, so every implementation of the GC collector must export the same sample types, frames and
labels. The test only uses the documented settings and real collections, and reads the exported
profile, never the collector.
"""

import pytest


@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_GC_TIME_ENABLED="true",
        DD_PROFILING_GC_TIME_PAUSE_EVENT_THRESHOLD_MS="5",
        DD_PROFILING_TIMELINE_ENABLED="true",
        DD_PROFILING_STACK_ENABLED="false",
        DD_PROFILING_LOCK_ENABLED="false",
        DD_PROFILING_MEMORY_ENABLED="false",
        DD_PROFILING_EXCEPTION_ENABLED="false",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_gc_time_profile_contract",
    ),
    err=None,
)
def test_gc_time_profile_contract() -> None:
    import gc
    import glob
    import os
    import threading
    import time
    from typing import Optional

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.pprof_utils import pprof_pb2

    generation_frames: dict[str, int] = {"gc.collect[gen=0]": 0, "gc.collect[gen=1]": 1, "gc.collect[gen=2]": 2}
    threshold_ns: int = 5_000_000
    long_pause_ns: int = 30_000_000
    clock_slack_ns: int = 1_000_000_000

    class CpuHeavyCycle:
        def __init__(self) -> None:
            self.cycle: CpuHeavyCycle = self

        def __del__(self) -> None:
            # A pause is the collecting thread's CPU time, so work instead of sleeping.
            deadline_ns: int = time.thread_time_ns() + long_pause_ns
            while time.thread_time_ns() < deadline_ns:
                pass

    # Only the collections below, all on this thread.
    gc.disable()
    prof: profiler.Profiler = profiler.Profiler()
    before_ns: int = time.time_ns()
    prof.start()
    gc.collect()
    # Young collections right after a full one are far below the threshold.
    for _ in range(3):
        gc.collect(0)
    # Garbage until the next collection, which runs its finalizer: one long full collection.
    CpuHeavyCycle()
    gc.collect()
    prof.stop()
    after_ns: int = time.time_ns()

    prefix: str = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile: pprof_pb2.Profile = pprof_utils.merge_profiles(
        [pprof_utils.parse_profile(filename) for filename in sorted(glob.glob(prefix + ".*.pprof"))]
    )

    units: dict[str, str] = {
        profile.string_table[sample_type.type]: profile.string_table[sample_type.unit]
        for sample_type in profile.sample_type
    }
    assert units.get("gc-time") == "nanoseconds", units
    assert units.get("gc-samples") == "count", units
    gc_time: int = pprof_utils.get_sample_type_index(profile, "gc-time")
    gc_samples: int = pprof_utils.get_sample_type_index(profile, "gc-samples")

    pauses: list[pprof_pb2.Sample] = []
    totals: list[pprof_pb2.Sample] = []
    configs: list[pprof_pb2.Sample] = []
    for sample in profile.sample:
        if not (sample.value[gc_time] or sample.value[gc_samples]):
            continue
        # One synthetic frame, in file "gc".
        assert len(sample.location_id) == 1, sample
        frame: pprof_utils.StackLocation = pprof_utils.get_location_from_id(profile, sample.location_id[0])
        assert frame.filename == "gc", frame
        event: Optional[str] = pprof_utils.get_str_label(profile, sample, "gc event")
        if frame.function_name == "gc.config":
            # The interval's explicit gc.collect() calls, as gc-samples with no gc-time.
            assert event is None, sample
            configs.append(sample)
            continue
        assert frame.function_name in generation_frames, frame
        if event is None:
            # A generation's window total. Without "gc event" the GC lane never takes it for a pause.
            totals.append(sample)
            continue
        # One pause, which the GC lane shows as an event of the collecting thread.
        assert event == "pause", sample
        assert pprof_utils.get_num_label(profile, sample, "gc generation") == generation_frames[frame.function_name]
        assert pprof_utils.get_num_label(profile, sample, "thread id") == threading.get_ident(), sample
        assert pprof_utils.get_str_label(profile, sample, "thread name") == "MainThread", sample
        end_ns: Optional[int] = pprof_utils.get_num_label(profile, sample, "end_timestamp_ns")
        assert end_ns is not None, sample
        assert before_ns - clock_slack_ns <= end_ns <= after_ns + clock_slack_ns, (before_ns, end_ns, after_ns)
        assert sample.value[gc_samples] == 1, sample
        assert sample.value[gc_time] >= threshold_ns, sample
        pauses.append(sample)

    assert totals, "no window total for the young collections below the threshold"
    assert any(
        pprof_utils.get_num_label(profile, sample, "gc generation") == 2 and sample.value[gc_time] >= long_pause_ns
        for sample in pauses
    ), pauses
    assert configs, "no gc.config sample"
    assert all(sample.value[gc_time] == 0 for sample in configs), configs
    assert sum(sample.value[gc_samples] for sample in configs) == 5, configs
