"""End-to-end test for greenlet attribution in the memory (allocation) profiler.

Before greenlet attribution, every allocation made by any greenlet running on a
gevent worker collapsed onto a single OS-thread lane: allocation samples carried
only a ``thread id`` label and no ``task id``. This made it impossible to tell
which greenlet was responsible for a memory spike.

This test spawns many greenlets that each allocate object-domain memory and
yield to the hub, then asserts that the resulting allocation samples carry
distinct ``task id`` labels (one per greenlet) -- i.e. the allocations are no
longer collapsed onto one lane.
"""

import os
import sys

import pytest


# gevent has version-specific incompatibilities with some CPython patch
# releases; mirror the guard used by tests/profiling/test_gevent.py.
GEVENT_COMPATIBLE_WITH_PYTHON_VERSION = os.getenv("DD_PROFILE_TEST_GEVENT", False) and (
    sys.version_info < (3, 11, 9) or sys.version_info >= (3, 12, 5)
)


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason=f"gevent is not compatible with Python {'.'.join(map(str, tuple(sys.version_info)[:3]))}",
)
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_ENABLED="1",
        DD_PROFILING_MEMORY_ENABLED="1",
        # Keep more allocation events per export so we reliably observe several
        # distinct greenlets in a single profile.
        DD_PROFILING_MEMORY_EVENTS_BUFFER="256",
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_gevent_memalloc",
    ),
    err=None,
)
def test_gevent_memalloc_greenlet_attribution() -> None:
    import os

    import gevent.monkey

    gevent.monkey.patch_all()

    import gevent

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils

    def alloc_in_greenlet(n: int) -> int:
        total = 0
        for _ in range(8):
            data = [{"i": i, "n": n, "payload": "x" * 48} for i in range(20000)]
            total += len(data)
            gevent.sleep(0)  # yield so greenlets interleave on the one OS thread
            del data
        return total

    p = profiler.Profiler()
    p.start()

    # Confirm the gevent profiling integration actually patched gevent; without
    # it there is no greenlet tracer to populate the current-task registry.
    import gevent.greenlet as _gg

    from ddtrace.profiling import _gevent as _gevent_mod

    assert _gg.Greenlet is _gevent_mod.Greenlet, "ddtrace gevent profiling support did not patch gevent"

    for _ in range(5):
        greenlets = [gevent.spawn(alloc_in_greenlet, i) for i in range(16)]
        gevent.joinall(greenlets)

    p.stop()

    output_filename = os.environ["DD_PROFILING_OUTPUT_PPROF"] + "." + str(os.getpid())
    profile = pprof_utils.parse_newest_profile(output_filename)

    def _stack_has(prof, sample, fnname: str) -> bool:
        for loc_id in sample.location_id:
            location = pprof_utils.get_location_with_id(prof, loc_id)
            for line in location.line:
                function = pprof_utils.get_function_with_id(prof, line.function_id)
                if fnname in prof.string_table[function.name]:
                    return True
        return False

    alloc_samples = pprof_utils.get_samples_with_value_type(profile, "alloc-space")
    assert len(alloc_samples) > 0, "no allocation samples were captured"

    # Allocation samples taken while a greenlet was running must carry a task id.
    greenlet_alloc_task_ids = set()
    for sample in alloc_samples:
        if not _stack_has(profile, sample, "alloc_in_greenlet"):
            continue
        task_id_label = pprof_utils.get_label_with_key(profile.string_table, sample, "task id")
        assert task_id_label is not None, "allocation sample inside a greenlet has no 'task id' label"
        greenlet_alloc_task_ids.add(task_id_label.num)

    assert len(greenlet_alloc_task_ids) >= 2, (
        "expected allocations to be attributed to multiple distinct greenlets "
        f"(task ids), but only saw {greenlet_alloc_task_ids!r}"
    )
