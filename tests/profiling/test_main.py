# -*- encoding: utf-8 -*-
import multiprocessing
import os
from pathlib import Path
import sys

import pytest

from tests.profiling.collector import lock_utils
from tests.profiling.collector import pprof_utils
from tests.utils import call_program


def test_call_script() -> None:
    env = os.environ.copy()
    env["DD_PROFILING_ENABLED"] = "1"
    stdout, stderr, exitcode, _ = call_program(
        "ddtrace-run", sys.executable, os.path.join(os.path.dirname(__file__), "simple_program.py"), env=env
    )
    if sys.platform == "win32":
        assert exitcode == 0, (stdout, stderr)
    else:
        assert exitcode == 42, (stdout, stderr)

    stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
    hello, _ = list(s.strip() for s in stdout.strip().split("\n"))
    assert hello == "hello world", stdout.strip()


@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_call_script_gevent():
    env = os.environ.copy()
    env["DD_PROFILING_ENABLED"] = "1"
    stdout, stderr, exitcode, pid = call_program(
        sys.executable, os.path.join(os.path.dirname(__file__), "simple_program_gevent.py"), env=env
    )
    assert exitcode == 0, (stdout, stderr)


def test_call_script_pprof_output(tmp_path: Path) -> None:
    """This checks if the pprof output and atexit register work correctly.

    The script does not run for one minute, so if the `stop_on_exit` flag is broken, this test will fail.
    """
    filename = str(tmp_path / "pprof")
    env = os.environ.copy()
    env["DD_PROFILING_OUTPUT_PPROF"] = filename
    env["DD_PROFILING_CAPTURE_PCT"] = "100"
    env["DD_PROFILING_ENABLED"] = "1"
    stdout, stderr, exitcode, _ = call_program(
        "ddtrace-run",
        sys.executable,
        os.path.join(os.path.dirname(__file__), "simple_program.py"),
        env=env,
    )
    if sys.platform == "win32":
        assert exitcode == 0, (stdout, stderr)
    else:
        assert exitcode == 42, (stdout, stderr)

    stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
    _, pid = list(s.strip() for s in stdout.strip().split("\n"))
    profile = pprof_utils.parse_newest_profile(f"{filename}.{pid}", allow_penultimate=True)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_fork(tmp_path: Path) -> None:
    filename = str(tmp_path / "pprof")
    env = os.environ.copy()
    env["DD_PROFILING_OUTPUT_PPROF"] = filename
    env["DD_PROFILING_CAPTURE_PCT"] = "100"
    stdout, _, exitcode, pid = call_program(
        "python", os.path.join(os.path.dirname(__file__), "simple_program_fork.py"), env=env
    )
    assert exitcode == 0
    stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
    child_pid = stdout.strip()
    profile = pprof_utils.parse_newest_profile(f"{filename}.{pid}", allow_penultimate=True)
    parent_expected_acquire_events = [
        pprof_utils.LockAcquireEvent(
            caller_name="<module>",
            filename="simple_program_fork.py",
            linenos=lock_utils.LineNo(create=11, acquire=12, release=28),
            lock_name="lock",
        ),
    ]
    parent_expected_release_events = [
        pprof_utils.LockReleaseEvent(
            caller_name="<module>",
            filename="simple_program_fork.py",
            linenos=lock_utils.LineNo(create=11, acquire=12, release=28),
            lock_name="lock",
        ),
    ]
    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=parent_expected_acquire_events,
        expected_release_events=parent_expected_release_events,
    )
    child_profile = pprof_utils.parse_newest_profile(f"{filename}.{child_pid}")
    # We expect the child profile to not have lock events from the parent process
    # Note that assert_lock_events function only checks that the given events
    # exists, and doesn't assert that other events don't exist.
    with pytest.raises(AssertionError):
        pprof_utils.assert_lock_events(
            child_profile,
            expected_acquire_events=parent_expected_acquire_events,
            expected_release_events=parent_expected_release_events,
        )
    pprof_utils.assert_lock_events(
        child_profile,
        expected_acquire_events=[
            # After fork(), we clear the samples in child, so we only have one
            # lock acquire event
            pprof_utils.LockAcquireEvent(
                caller_name="<module>",
                filename="simple_program_fork.py",
                linenos=lock_utils.LineNo(create=24, acquire=25, release=26),
                lock_name="lock",
            ),
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="<module>",
                filename="simple_program_fork.py",
                linenos=lock_utils.LineNo(create=11, acquire=12, release=21),
                lock_name="lock",
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="<module>",
                filename="simple_program_fork.py",
                linenos=lock_utils.LineNo(create=24, acquire=25, release=26),
                lock_name="lock",
            ),
        ],
    )


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_fork_gevent() -> None:
    env = os.environ.copy()
    _, _, exitcode, _ = call_program("python", os.path.join(os.path.dirname(__file__), "gevent_fork.py"), env=env)
    assert exitcode == 0


methods = multiprocessing.get_all_start_methods()


@pytest.mark.parametrize(
    "method",
    set(methods) - {"forkserver", "fork"},
)
def test_multiprocessing(method: str, tmp_path: Path) -> None:
    filename = str(tmp_path / "pprof")
    env = os.environ.copy()
    env["DD_PROFILING_OUTPUT_PPROF"] = filename
    env["DD_PROFILING_ENABLED"] = "1"
    env["DD_PROFILING_CAPTURE_PCT"] = "100"
    stdout, stderr, exitcode, _ = call_program(
        "ddtrace-run",
        sys.executable,
        os.path.join(os.path.dirname(__file__), "_test_multiprocessing.py"),
        method,
        env=env,
    )
    assert exitcode == 0, (stdout, stderr)
    stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
    pid, child_pid = list(s.strip() for s in stdout.strip().split("\n"))
    profile = pprof_utils.parse_newest_profile(f"{filename}.{pid}")
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0
    child_profile = pprof_utils.parse_newest_profile(filename + "." + str(child_pid))
    child_samples = pprof_utils.get_samples_with_value_type(child_profile, "cpu-time")
    assert len(child_samples) > 0


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="1"),
    err=lambda _: "RuntimeError: the memalloc module is already started" not in _,
)
def test_memalloc_no_init_error_on_fork() -> None:
    import os

    pid = os.fork()
    if not pid:
        exit(0)
    os.waitpid(pid, 0)


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="1",
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="1",
    ),
    out="OK\n",
    err=None,
)
def test_profiler_start_up_with_module_clean_up_in_protobuf_app() -> None:
    # This can cause segfaults if we do module clean up with later versions of
    # protobuf. This is a regression test.
    from google.protobuf import empty_pb2  # noqa:F401

    print("OK")


@pytest.mark.skipif(sys.platform == "win32", reason="Signal-based exit codes only on Unix")
def test_no_segfault_on_quick_shutdown() -> None:
    """Test that the profiler doesn't segfault when Python exits quickly.

    This is a regression test for a race condition where the native sampling thread
    could access Python interpreter structures during finalization, causing a segfault.

    The fix adds Py_IsFinalizing() checks in the sampling loop to exit early when
    Python is shutting down.

    We run the test multiple times to increase the chance of catching the race condition.
    """
    import signal

    SIGSEGV_EXIT_CODE = 128 + signal.SIGSEGV  # 139 on Linux

    # Run multiple iterations to catch intermittent race conditions
    for i in range(5):
        env = os.environ.copy()
        env["DD_PROFILING_ENABLED"] = "1"
        # Use a very short sampling interval to increase chance of race
        env["DD_PROFILING_STACK_V2_INTERVAL"] = "0.001"

        # Run a script that starts profiling and exits immediately
        stdout, stderr, exitcode, _ = call_program(
            "ddtrace-run",
            sys.executable,
            "-c",
            # Start profiler and exit immediately - this triggers the race condition
            "import ddtrace.profiling.auto; import sys; sys.exit(0)",
            env=env,
            timeout=30,
        )

        # Check for segfault (exit code 139 = 128 + SIGSEGV)
        assert exitcode != SIGSEGV_EXIT_CODE, (
            f"Iteration {i}: Profiler segfaulted during shutdown! "
            f"Exit code: {exitcode}, stdout: {stdout}, stderr: {stderr}"
        )
        # Also check for other crash signals
        assert exitcode >= 0, (
            f"Iteration {i}: Profiler crashed during shutdown! "
            f"Exit code: {exitcode}, stdout: {stdout}, stderr: {stderr}"
        )
