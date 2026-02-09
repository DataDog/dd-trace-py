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
    stdout, stderr, exitcode, pid = call_program(
        sys.executable, os.path.join(os.path.dirname(__file__), "simple_program_fork.py"), env=env
    )
    assert exitcode == 0, stderr
    stdout = stdout.decode() if isinstance(stdout, bytes) else stdout
    child_pid = stdout.strip()

    # Merge all pprof files for parent PID
    all_files_for_pid = sorted([x for x in os.listdir(tmp_path) if ".pprof" in x])
    parent_files = [str(tmp_path / f) for f in all_files_for_pid if f".{str(pid)}." in f]
    parent_profiles = [pprof_utils.parse_profile(f) for f in parent_files]
    parent_profile = pprof_utils.merge_profiles(parent_profiles)

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
        parent_profile,
        expected_acquire_events=parent_expected_acquire_events,
        expected_release_events=parent_expected_release_events,
        print_samples_on_failure=True,
    )

    # Merge all pprof files for child PID
    child_files = [str(tmp_path / f) for f in all_files_for_pid if f".{str(child_pid)}." in f]
    child_profile = pprof_utils.merge_profiles([pprof_utils.parse_profile(f) for f in child_files])
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
        print_samples_on_failure=True,
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
