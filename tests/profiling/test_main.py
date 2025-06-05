# -*- encoding: utf-8 -*-
import multiprocessing
import os
import sys

import pytest

from tests.utils import call_program

from . import utils


def test_call_script(monkeypatch):
    # Set a very short timeout to exit fast
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")
    stdout, stderr, exitcode, _ = call_program(
        "ddtrace-run", sys.executable, os.path.join(os.path.dirname(__file__), "simple_program.py")
    )
    if sys.platform == "win32":
        assert exitcode == 0, (stdout, stderr)
    else:
        assert exitcode == 42, (stdout, stderr)
    hello, interval, _ = list(s.strip() for s in stdout.decode().strip().split("\n"))
    assert hello == "hello world", stdout.decode().strip()
    assert float(interval) >= 0.01, stdout.decode().strip()


@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_call_script_gevent(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    stdout, stderr, exitcode, pid = call_program(
        sys.executable, os.path.join(os.path.dirname(__file__), "simple_program_gevent.py")
    )
    assert exitcode == 0, (stdout, stderr)


def test_call_script_pprof_output(tmp_path, monkeypatch):
    """This checks if the pprof output and atexit register work correctly.

    The script does not run for one minute, so if the `stop_on_exit` flag is broken, this test will fail.
    """
    filename = str(tmp_path / "pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_CAPTURE_PCT", "1")
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")
    stdout, stderr, exitcode, _ = call_program(
        "ddtrace-run", sys.executable, os.path.join(os.path.dirname(__file__), "simple_program.py")
    )
    if sys.platform == "win32":
        assert exitcode == 0, (stdout, stderr)
    else:
        assert exitcode == 42, (stdout, stderr)
    hello, interval, pid = list(s.strip() for s in stdout.decode().strip().split("\n"))
    utils.check_pprof_file(filename + "." + str(pid))


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
def test_fork(tmp_path, monkeypatch):
    filename = str(tmp_path / "pprof")
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_CAPTURE_PCT", "100")
    stdout, stderr, exitcode, pid = call_program(
        "python", os.path.join(os.path.dirname(__file__), "simple_program_fork.py")
    )
    assert exitcode == 0
    child_pid = stdout.decode().strip()
    utils.check_pprof_file(filename + "." + str(pid))
    utils.check_pprof_file(filename + "." + str(child_pid), sample_type="lock-release")


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_fork_gevent(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    stdout, stderr, exitcode, pid = call_program("python", os.path.join(os.path.dirname(__file__), "gevent_fork.py"))
    assert exitcode == 0


methods = multiprocessing.get_all_start_methods()


@pytest.mark.parametrize(
    "method",
    set(methods) - {"forkserver", "fork"},
)
def test_multiprocessing(method, tmp_path, monkeypatch):
    filename = str(tmp_path / "pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")
    monkeypatch.setenv("DD_PROFILING_CAPTURE_PCT", "1")
    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "0.1")
    stdout, stderr, exitcode, _ = call_program(
        "ddtrace-run",
        sys.executable,
        os.path.join(os.path.dirname(__file__), "_test_multiprocessing.py"),
        method,
    )
    assert exitcode == 0, (stdout, stderr)
    pid, child_pid = list(s.strip() for s in stdout.decode().strip().split("\n"))
    utils.check_pprof_file(filename + "." + str(pid))
    utils.check_pprof_file(filename + "." + str(child_pid), sample_type="wall-time")


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


@pytest.mark.skipif(sys.version_info[:2] == (3, 9), reason="This test is flaky on Python 3.9")
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
