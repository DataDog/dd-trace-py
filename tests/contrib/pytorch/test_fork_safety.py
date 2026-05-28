"""Layer Zero state is reset in `fork`-ed children so the child can
bootstrap its own rank span without inheriting parent state.

RateTicker and DogStatsD-related fork-reset assertions were removed when
the Layer Zero DogStatsD emission path was deleted. The remaining checks
verify that the duration reservoir and distributed state are properly
cleared across fork.
"""

import multiprocessing as mp
import os
from unittest import mock

import pytest

from ddtrace.contrib.internal.pytorch import _device
from ddtrace.contrib.internal.pytorch import _distributed
from ddtrace.contrib.internal.pytorch import _metrics
from ddtrace.contrib.internal.pytorch import _rank_root
from ddtrace.contrib.internal.pytorch import _test_helpers as _th


def _child_assert_fresh(q):
    # Verify parent's Layer Zero reservoir state and distributed state were reset.
    try:
        assert _th.current_rank_span() is None, "rank span leaked into child"
        assert _distributed._state["bootstrapped"] is False, "_state['bootstrapped'] leaked into child"
        assert _distributed._state.get("resolver") is None, "_state['resolver'] leaked into child"
        # Duration reservoir must be empty — no pre-fork durations carry over.
        summary = _metrics.summary_snapshot_and_reset()
        assert summary == {}, "duration reservoir leaked into child: %s" % summary
        q.put("ok")
    except AssertionError as e:
        q.put(str(e))


@pytest.mark.skipif(os.name != "posix", reason="fork is POSIX-only")
def test_fork_resets_layer_zero_state():
    _th.reset_device_cache()
    _th.reset_metrics_state()
    _th.close_rank_root()
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-parent"),
    ):
        _device.discover(local_rank=0)
    # Parent-side state we want the child to NOT inherit
    _metrics.record_collective(op="allreduce", duration_ms=1.0, bytes_count=4096)
    _rank_root.open(rank=0, world_size=1, framework="none", training_job_id="job-X")
    # Set _distributed._state so the child's fork hook must reset it.
    _distributed._state.update({"bootstrapped": True, "resolver": object()})

    ctx = mp.get_context("fork")
    q = ctx.Queue()
    p = ctx.Process(target=_child_assert_fresh, args=(q,))
    p.start()
    p.join(timeout=10)
    result = q.get(timeout=1)

    _rank_root.close()
    # Restore _distributed._state so other tests are not affected.
    _distributed._state.update({"bootstrapped": False, "resolver": None})
    assert result == "ok", result


@pytest.mark.skipif(os.name != "posix", reason="fork is POSIX-only")
def test_run_metadata_cleared_after_fork(tmp_path):
    """Use a file marker rather than multiprocessing.Queue: Queue's
    feeder thread is fork-unsafe and os._exit skips flush.
    """
    import multiprocessing

    from ddtrace.contrib.internal.pytorch import _utils
    from ddtrace.contrib.internal.pytorch._utils import get_cached_run_metadata
    from ddtrace.contrib.internal.pytorch._utils import set_cached_run_metadata

    set_cached_run_metadata(run_name="parent-run", submission_id="parent-sub", metadata={"k": "v"})

    marker = tmp_path / "child_metadata.txt"

    def child(path):
        snap = get_cached_run_metadata()
        path.write_text("EMPTY" if len(snap) == 0 else "STALE:" + repr(dict(snap)))
        os._exit(0)

    try:
        ctx = multiprocessing.get_context("fork")
        p = ctx.Process(target=child, args=(marker,))
        p.start()
        p.join(timeout=5)
        assert p.exitcode == 0, f"child exited with code {p.exitcode}"
        content = marker.read_text()
        assert content == "EMPTY", f"child saw stale metadata: {content}"
    finally:
        _utils.clear_cached_run_metadata()
