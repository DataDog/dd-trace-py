# -*- encoding: utf-8 -*-
import multiprocessing
import os
import sys

import pytest

from tests.profiling.collector import lock_utils
from tests.profiling.collector import pprof_utils
from tests.utils import call_program
from tests.utils import flaky


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_call_script(stack_v2_enabled):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")
    env = os.environ.copy()
    env["DD_PROFILING_ENABLED"] = "1"
    env["DD_PROFILING_STACK_V2_ENABLED"] = "1" if stack_v2_enabled else "0"
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
    assert stack_v2 == str(stack_v2_enabled)


@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_call_script_gevent(stack_v2_enabled):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")
    if sys.version_info[:2] == (3, 8) and stack_v2_enabled:
        pytest.skip("this test is flaky on 3.8 with stack v2")
    env = os.environ.copy()
    env["DD_PROFILING_ENABLED"] = "1"
    env["DD_PROFILING_STACK_V2_ENABLED"] = "1" if stack_v2_enabled else "0"
    stdout, stderr, exitcode, pid = call_program(
        sys.executable, os.path.join(os.path.dirname(__file__), "simple_program_gevent.py"), env=env
    )
    assert exitcode == 0, (stdout, stderr)


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
def test_call_script_pprof_output(stack_v2_enabled, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    """This checks if the pprof output and atexit register work correctly.

    The script does not run for one minute, so if the `stop_on_exit` flag is broken, this test will fail.
    """
    filename = str(tmp_path / "pprof")
    env = os.environ.copy()
    env["DD_PROFILING_OUTPUT_PPROF"] = filename
    env["DD_PROFILING_CAPTURE_PCT"] = "1"
    env["DD_PROFILING_ENABLED"] = "1"
    env["DD_PROFILING_STACK_V2_ENABLED"] = "1" if stack_v2_enabled else "0"
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
    _, _, _, pid = list(s.strip() for s in stdout.decode().strip().split("\n"))
    profile = pprof_utils.parse_profile(filename + "." + str(pid))
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_fork(stack_v2_enabled, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")

    filename = str(tmp_path / "pprof")
    env = os.environ.copy()
    env["DD_PROFILING_OUTPUT_PPROF"] = filename
    env["DD_PROFILING_CAPTURE_PCT"] = "100"
    env["DD_PROFILING_STACK_V2_ENABLED"] = "1" if stack_v2_enabled else "0"
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


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_fork_gevent(stack_v2_enabled):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")
    env = os.environ.copy()
    env["DD_PROFILING_STACK_V2_ENABLED"] = "1" if stack_v2_enabled else "0"
    stdout, stderr, exitcode, pid = call_program(
        "python", os.path.join(os.path.dirname(__file__), "../profiling", "gevent_fork.py"), env=env
    )
    assert exitcode == 0


methods = multiprocessing.get_all_start_methods()


@pytest.mark.parametrize("stack_v2_enabled", [True, False])
@pytest.mark.parametrize(
    "method",
    set(methods) - {"forkserver", "fork"},
)
def test_multiprocessing(stack_v2_enabled, method, tmp_path):
    if sys.version_info[:2] == (3, 7) and stack_v2_enabled:
        pytest.skip("stack_v2 is not supported on Python 3.7")
    filename = str(tmp_path / "pprof")
    env = os.environ.copy()
    env["DD_PROFILING_OUTPUT_PPROF"] = filename
    env["DD_PROFILING_ENABLED"] = "1"
    env["DD_PROFILING_CAPTURE_PCT"] = "1"
    env["DD_PROFILING_STACK_V2_ENABLED"] = "1" if stack_v2_enabled else "0"
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


@flaky(1731959126)  # Marking as flaky so it will show up in flaky reports
@pytest.mark.skipif(os.environ.get("GITLAB_CI") == "true", reason="Hanging and failing in GitLab CI")
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


# Not parametrizing with stack_v2_enabled as subprocess mark doesn't support
# parametrized tests and this only tests our start up code.
@pytest.mark.subprocess(
    ddtrace_run=True,
    env=dict(
        DD_PROFILING_ENABLED="1",
        DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE="1",
    ),
    out="OK\n",
    err=None,
)
def test_profiler_start_up_with_module_clean_up_in_protobuf_app():
    # This can cause segfaults if we do module clean up with later versions of
    # protobuf. This is a regression test.
    from google.protobuf import empty_pb2  # noqa:F401

    print("OK")
