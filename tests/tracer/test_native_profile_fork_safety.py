"""Stage 3 of OPTION_C_ONE_HOP_PLAN.md: fork-safety reproducer for the PyO3 `DdProfile`.

`dd_wrapper`'s `ProfilerState` installs `pthread_atfork` handlers
(`profiler_state.cpp`) specifically because a raw `DictArc<ProfilesDictionary>`
handle and a locked `parking_lot::Mutex<Profile>` are not safe to use in a
forked child without intervention: the dictionary's handles reference
allocator/mapping state that doesn't exist post-fork, and a mutex held by a
(now-nonexistent) thread at fork time has undefined OS-level state in the
child.

The PyO3 `DdProfile` (`profiling_sample.rs`) has none of that handling yet.
This test exercises `os.fork()` directly against the current, unmodified
`DdProfile` to check whether that gap is actually reachable.

Empirically it isn't, for this code path: `DdProfile`/`SampleHandle` never
call `py.detach()` (pyo3's GIL-release), so a Python thread can never be
mid-mutation of the dictionary/profile when another thread calls
`os.fork()` -- the forking thread already holds the GIL, and nothing here
lets go of it. This test passes and is kept as a regression guard for that
property, not as a fork-safety fix verification (there's nothing to fix on
this surface yet). `ProfileUploaderPy.upload()` in `profiling_uploader.rs`
does call `py.detach()` around its blocking HTTP send and holds a
`Mutex<Option<CancellationToken>>` across that window -- that is where a
real fork hazard exists, and where `pthread_atfork`-equivalent handling
(mirroring `ProfilerState::prefork()`'s cancel-and-wait loop) actually
belongs.
"""

import pytest


native_profiling = pytest.importorskip(
    "ddtrace.internal.native._native", reason="requires the profiling feature of the _native extension"
)

pytestmark = pytest.mark.skipif(
    not hasattr(native_profiling, "DdProfile"), reason="DdProfile is only built with the profiling Cargo feature"
)


@pytest.mark.subprocess()
def test_fork_child_can_sample_and_serialize_independently():
    import os
    import tempfile

    from ddtrace.internal.native import _native
    from tests.profiling.collector import pprof_utils

    tmp_dir = tempfile.mkdtemp()
    child_pprof_path = f"{tmp_dir}/child.pprof"
    parent_pprof_path = f"{tmp_dir}/parent.pprof"
    child_error_path = f"{tmp_dir}/child_error.txt"

    profile = _native.DdProfile(_native.SAMPLE_TYPE_ALL, 64)
    handle = profile.start_sample()
    handle.push_frame("pre_fork", "app.py", 0, 1)
    handle.push_walltime(1_000_000, 1)
    profile.add_sample(handle)

    pid = os.fork()
    if pid == 0:
        # Child: exercise the same DdProfile object post-fork. Any exception
        # here (rather than a hard crash) is captured to a file so the parent
        # can report it, since pytest can't observe exceptions raised in a
        # forked child directly.
        try:
            child_handle = profile.start_sample()
            child_handle.push_frame("child_frame", "app.py", 0, 2)
            child_handle.push_walltime(1_000_000, 1)
            profile.add_sample(child_handle)
            buffer, _start_ns, _end_ns = profile.serialize(None)
            with open(child_pprof_path, "wb") as f:
                f.write(buffer)
        except BaseException as exc:  # noqa: BLE001
            with open(child_error_path, "w") as f:
                f.write(repr(exc))
        os._exit(0)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status), f"child did not exit cleanly, status={status}"
    assert os.WEXITSTATUS(status) == 0, f"child process exited with nonzero status={os.WEXITSTATUS(status)}"
    assert not os.path.exists(child_error_path), (
        f"child raised an exception operating on DdProfile post-fork: {open(child_error_path).read()}"
    )

    parent_handle = profile.start_sample()
    parent_handle.push_frame("parent_frame", "app.py", 0, 3)
    parent_handle.push_walltime(1_000_000, 1)
    profile.add_sample(parent_handle)
    buffer, _start_ns, _end_ns = profile.serialize(None)
    with open(parent_pprof_path, "wb") as f:
        f.write(buffer)

    assert os.path.exists(child_pprof_path), "child never produced a pprof"
    child_prof = pprof_utils.parse_profile(child_pprof_path)
    parent_prof = pprof_utils.parse_profile(parent_pprof_path)

    assert len(child_prof.sample) >= 1
    assert len(parent_prof.sample) >= 1
