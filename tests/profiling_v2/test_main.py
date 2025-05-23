# -*- encoding: utf-8 -*-
import multiprocessing
import os
import sys

import pytest

from tests.profiling.collector import lock_utils
from tests.profiling.collector import pprof_utils
from tests.utils import call_program


def test_call_script():
    env = os.environ.copy()
    env["DD_PROFILING_ENABLED"] = "1"
    stdout, stderr, exitcode, _ = call_program(
        "ddtrace-run", sys.executable, os.path.join(os.path.dirname(__file__), "simple_program.py"), env=env
    )
    if sys.platform == "win32":
        assert exitcode == 0, (stdout, stderr)
    else:
        assert exitcode == 42, (stdout, stderr)
    hello, interval, pid, stack_v2 = list(s.strip() for s in stdout.decode().strip().split("\n"))
    assert hello == "hello world", stdout.decode().strip()
    assert float(interval) >= 0.01, stdout.decode().strip()
    assert stack_v2 == str(True)


@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_call_script_gevent():
    if sys.version_info[:2] == (3, 8):
        pytest.skip("this test is flaky on 3.8 with stack v2")
    env = os.environ.copy()
    env["DD_PROFILING_ENABLED"] = "1"
    stdout, stderr, exitcode, pid = call_program(
        sys.executable, os.path.join(os.path.dirname(__file__), "simple_program_gevent.py"), env=env
    )
    assert exitcode == 0, (stdout, stderr)


def test_call_script_pprof_output(tmp_path):
    """This checks if the pprof output and atexit register work correctly.

    The script does not run for one minute, so if the `stop_on_exit` flag is broken, this test will fail.
    """
    filename = str(tmp_path / "pprof")
    env = os.environ.copy()
    env["DD_PROFILING_OUTPUT_PPROF"] = filename
    env["DD_PROFILING_CAPTURE_PCT"] = "1"
    env["DD_PROFILING_ENABLED"] = "1"
    stdout, stderr, exitcode, _ = call_program(
        "ddtrace-run",
        sys.executable,
        os.path.join(os.path.dirname(__file__), "../profiling", "simple_program.py"),
        env=env,
    )
    if sys.platform == "win32":
        assert exitcode == 0, (stdout, stderr)
    else:
        assert exitcode == 42, (stdout, stderr)
    _, _, pid = list(s.strip() for s in stdout.decode().strip().split("\n"))
    profile = pprof_utils.parse_profile(filename + "." + str(pid))
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_fork(tmp_path):
    filename = str(tmp_path / "pprof")
    env = os.environ.copy()
    env["DD_PROFILING_OUTPUT_PPROF"] = filename
    env["DD_PROFILING_CAPTURE_PCT"] = "100"
    stdout, stderr, exitcode, pid = call_program(
        "python", os.path.join(os.path.dirname(__file__), "simple_program_fork.py"), env=env
    )
    assert exitcode == 0
    child_pid = stdout.decode().strip()
    profile = pprof_utils.parse_profile(filename + "." + str(pid))
    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="<module>",
                filename="simple_program_fork.py",
                linenos=lock_utils.LineNo(create=11, acquire=12, release=28),
                lock_name="lock",
            ),
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="<module>",
                filename="simple_program_fork.py",
                linenos=lock_utils.LineNo(create=11, acquire=12, release=28),
                lock_name="lock",
            ),
        ],
    )
    child_profile = pprof_utils.parse_profile(filename + "." + str(child_pid))
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
def test_fork_gevent():
    env = os.environ.copy()
    stdout, stderr, exitcode, pid = call_program(
        "python", os.path.join(os.path.dirname(__file__), "../profiling", "gevent_fork.py"), env=env
    )
    assert exitcode == 0


methods = multiprocessing.get_all_start_methods()


@pytest.mark.parametrize(
    "method",
    set(methods) - {"forkserver", "fork"},
)
def test_multiprocessing(method, tmp_path):
    filename = str(tmp_path / "pprof")
    env = os.environ.copy()
    env["DD_PROFILING_OUTPUT_PPROF"] = filename
    env["DD_PROFILING_ENABLED"] = "1"
    env["DD_PROFILING_CAPTURE_PCT"] = "1"
    stdout, stderr, exitcode, _ = call_program(
        "ddtrace-run",
        sys.executable,
        os.path.join(os.path.dirname(__file__), "../profiling", "_test_multiprocessing.py"),
        method,
        env=env,
    )
    assert exitcode == 0, (stdout, stderr)
    pid, child_pid = list(s.strip() for s in stdout.decode().strip().split("\n"))
    profile = pprof_utils.parse_profile(filename + "." + str(pid))
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0
    child_profile = pprof_utils.parse_profile(filename + "." + str(child_pid))
    child_samples = pprof_utils.get_samples_with_value_type(child_profile, "cpu-time")
    assert len(child_samples) > 0


@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(DD_PROFILING_ENABLED="1"),
    err=lambda _: "RuntimeError: the memalloc module is already started" not in _,
)
def test_memalloc_no_init_error_on_fork():
    import os

    pid = os.fork()
    if not pid:
        exit(0)
    os.waitpid(pid, 0)
