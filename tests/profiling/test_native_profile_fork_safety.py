"""Stage 3 of OPTION_C_ONE_HOP_PLAN.md: fork-safety reproducer for the PyO3 `DdProfile`.

`test_upload_via_native_survives_fork_in_flight` below covers the real hazard this module's
own docstring points at: `ProfileUploaderPy`'s `UPLOAD_CANCEL` mutex, now that
`profiling_uploader.rs` registers `profile_uploader_before_fork`/`_after_fork_parent`/
`_after_fork_child` with `ddtrace.internal.forksafe` (mirroring dd_wrapper's
`ProfilerState::prefork()`/`postfork_parent()`/`postfork_child()`). Per
`docs/native-code-review.md` §9 ("does the test fail without the fix?") and its guidance
against flaky race-based fork tests, this does not try to win a race with `fork()` -- it
verifies the reset path itself: call `send_blocking()` (taking the lock), fork, then assert a
subsequent `send_blocking()` in *both* parent and child completes (doesn't deadlock on an
inherited lock) and produces correct output.

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
            buffer, _start_ns, _end_ns, _endpoint_counts = profile.serialize(None)
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
    buffer, _start_ns, _end_ns, _endpoint_counts = profile.serialize(None)
    with open(parent_pprof_path, "wb") as f:
        f.write(buffer)

    assert os.path.exists(child_pprof_path), "child never produced a pprof"
    child_prof = pprof_utils.parse_profile(child_pprof_path)
    parent_prof = pprof_utils.parse_profile(parent_pprof_path)

    assert len(child_prof.sample) >= 1
    assert len(parent_prof.sample) >= 1


pytestmark_uploader = pytest.mark.skipif(
    not hasattr(native_profiling, "ProfileUploader"),
    reason="ProfileUploader is only built with the profiling Cargo feature",
)


@pytestmark_uploader
@pytest.mark.subprocess(timeout=30)
def test_upload_via_native_survives_fork_in_flight():
    import os
    import tempfile

    from ddtrace.internal.datadog.profiling.ddup import _ddup
    from ddtrace.internal.native._native import ProfileUploader

    tmp_dir = tempfile.mkdtemp()

    def upload_once(dump_name: str) -> None:
        # A `file://` URL, NOT `output_filename`: `output_filename` short-circuits inside
        # `send_blocking` (straight to `write_to_file`) and never reaches the exporter, so it
        # would never touch the `UPLOAD_CANCEL` mutex this test exists to exercise. `file://`
        # goes through the real `ProfileExporter` -- and therefore through the
        # cancellation-token exchange under the lock -- while dumping the HTTP request to disk
        # instead of hitting the network, the same trick tests/tracer/test_native_profile_
        # uploader.py uses.
        #
        # The uploader is constructed *inside* this function, i.e. after the fork in the child:
        # `send_blocking` stands up a tokio runtime, and a runtime inherited across `fork()`
        # would hang the child for reasons that have nothing to do with the mutex.
        dump_path = f"{tmp_dir}/{dump_name}.http"
        uploader = ProfileUploader(
            library_name="dd-trace-py",
            library_version="test",
            family="python",
            url=f"file://{dump_path}",
        )
        status = uploader.send_blocking(buffer=b"pprof-bytes", start_ns=0, end_ns=1)
        assert status == 200
        with open(dump_path, "rb") as f:
            assert b"pprof-bytes" in f.read()

    # Registers profile_uploader_before_fork/_after_fork_parent/_after_fork_child with
    # ddtrace.internal.forksafe, same as a real ddup.config(use_native_uploader=True) call.
    _ddup._register_native_uploader_fork_hooks()

    # Takes and releases UPLOAD_CANCEL once before forking, same as any real upload -- so the
    # mutex is genuinely on the exercised path here, and the before_fork/after_fork_* pair below
    # runs against a mutex that has actually been used.
    upload_once("pre-fork")

    pid = os.fork()
    if pid == 0:
        # Child: if the fork hooks failed to release UPLOAD_CANCEL (or released it into a bad
        # state), this call hangs -- the subprocess-level timeout above is what would catch that.
        try:
            upload_once("child")
        except BaseException as exc:  # noqa: BLE001
            with open(f"{tmp_dir}/child_error.txt", "w") as f:
                f.write(repr(exc))
        os._exit(0)

    _, status = os.waitpid(pid, 0)
    assert os.WIFEXITED(status), f"child did not exit cleanly, status={status}"
    assert os.WEXITSTATUS(status) == 0, f"child process exited with nonzero status={os.WEXITSTATUS(status)}"
    assert not os.path.exists(f"{tmp_dir}/child_error.txt"), (
        f"child raised uploading post-fork: {open(f'{tmp_dir}/child_error.txt').read()}"
    )

    # Parent: same check -- the before_fork/after_fork_parent pair must also leave the lock
    # usable, not just the child path.
    upload_once("post-fork-parent")


@pytestmark_uploader
@pytest.mark.subprocess(timeout=30)
def test_upload_after_fork_with_upload_cancel_locked_across_fork():
    """The fail-without-the-fix half of the pair above.

    The test above proves the mutex is on the exercised path and catches an asymmetric hook set
    (a before_fork that nothing releases), but it cannot fail if all three hooks are simply
    unregistered: by the time it forks, the pre-fork upload has already released UPLOAD_CANCEL,
    so the child inherits an unlocked mutex and would be fine either way. Detecting that case
    "naturally" needs a thread racing fork(), which docs/native-code-review.md §9 warns off as
    inherently flaky.

    So this drives the three hooks by hand instead of registering them, putting the process
    deterministically into the exact state the hooks exist for: UPLOAD_CANCEL locked at the
    instant of fork(). If profile_uploader_after_fork_child stopped calling force_unlock(), the
    child's send_blocking() would block forever on a mutex whose owner doesn't exist in the
    child, and the subprocess timeout above is what reports it; likewise for
    profile_uploader_after_fork_parent on the parent side.

    Hooks are driven manually rather than via _register_native_uploader_fork_hooks(): with them
    registered, the atfork before_fork handler would run against a mutex this test already
    locked, spin out its bounded retry loop, and skip the force_unlock -- deadlocking the child
    for a reason that isn't the one under test.
    """
    import os
    import signal
    import tempfile
    import time

    from ddtrace.internal.native._native import ProfileUploader
    from ddtrace.internal.native._native import profile_uploader_after_fork_child
    from ddtrace.internal.native._native import profile_uploader_after_fork_parent
    from ddtrace.internal.native._native import profile_uploader_before_fork

    tmp_dir = tempfile.mkdtemp()

    def upload_once(dump_name: str) -> None:
        # Constructed after the fork, for the inherited-tokio-runtime reason spelled out in the
        # test above. No pre-fork upload happens in this test at all, so no runtime can exist
        # before fork() to confound a hang.
        dump_path = f"{tmp_dir}/{dump_name}.http"
        uploader = ProfileUploader(
            library_name="dd-trace-py",
            library_version="test",
            family="python",
            url=f"file://{dump_path}",
        )
        status = uploader.send_blocking(buffer=b"pprof-bytes", start_ns=0, end_ns=1)
        assert status == 200
        with open(dump_path, "rb") as f:
            assert b"pprof-bytes" in f.read()

    # Lock UPLOAD_CANCEL and hold it across fork(), exactly as the registered atfork handler
    # would.
    profile_uploader_before_fork()

    pid = os.fork()
    if pid == 0:
        try:
            profile_uploader_after_fork_child()
            # Hangs forever (until the subprocess timeout) if the child hook left the inherited
            # mutex locked.
            upload_once("child")
        except BaseException as exc:  # noqa: BLE001
            with open(f"{tmp_dir}/child_error.txt", "w") as f:
                f.write(repr(exc))
        os._exit(0)

    profile_uploader_after_fork_parent()
    # Same for the parent side: this deadlocks if after_fork_parent didn't release the lock.
    upload_once("parent")

    # Bounded wait rather than a blocking waitpid(): a child deadlocked on UPLOAD_CANCEL would
    # otherwise hang the parent here too, and the child also holds the inherited stdout/stderr
    # pipes open, so the harness's subprocess timeout would surface it as a timeout rather than
    # as a named failure. Reap it ourselves and say what actually went wrong.
    deadline = time.monotonic() + 15
    while True:
        waited_pid, status = os.waitpid(pid, os.WNOHANG)
        if waited_pid == pid:
            break
        if time.monotonic() > deadline:
            os.kill(pid, signal.SIGKILL)
            os.waitpid(pid, 0)
            raise AssertionError(
                "child never finished its post-fork upload -- profile_uploader_after_fork_child "
                "most likely left UPLOAD_CANCEL locked, so send_blocking() deadlocked on it"
            )
        time.sleep(0.05)

    assert os.WIFEXITED(status), f"child did not exit cleanly, status={status}"
    assert os.WEXITSTATUS(status) == 0, f"child process exited with nonzero status={os.WEXITSTATUS(status)}"
    assert not os.path.exists(f"{tmp_dir}/child_error.txt"), (
        f"child raised uploading post-fork: {open(f'{tmp_dir}/child_error.txt').read()}"
    )
