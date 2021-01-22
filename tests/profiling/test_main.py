import gzip
import os
import subprocess

import pytest

from ddtrace.profiling.exporter import pprof_pb2


def call_program(*args):
    subp = subprocess.Popen(
        args,
        stdout=subprocess.PIPE,
        close_fds=True,
    )
    stdout, stderr = subp.communicate()
    return stdout, stderr, subp.wait(), subp.pid


def test_call_script(monkeypatch):
    # Set a very short timeout to exit fast
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    stdout, stderr, exitcode, pid = call_program(
        "pyddprofile", os.path.join(os.path.dirname(__file__), "simple_program.py")
    )
    assert exitcode == 42
    hello, interval, stacks = stdout.decode().strip().split("\n")
    assert hello == "hello world"
    assert float(interval) >= 0.01
    assert int(stacks) >= 1


@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_call_script_gevent(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    _, _, exitcode, pid = call_program("python", os.path.join(os.path.dirname(__file__), "simple_program_gevent.py"))
    assert exitcode == 0


def check_pprof_file(filename):
    with gzip.open(filename, "rb") as f:
        content = f.read()
    p = pprof_pb2.Profile()
    p.ParseFromString(content)
    assert len(p.sample_type) == 10
    assert p.string_table[p.sample_type[0].type] == "cpu-samples"


def test_call_script_pprof_output(tmp_path, monkeypatch):
    """This checks if the pprof output and atexit register work correctly.

    The script does not run for one minute, so if the `stop_on_exit` flag is broken, this test will fail.
    """
    filename = str(tmp_path / "pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_CAPTURE_PCT", "1")
    _, _, exitcode, pid = call_program("pyddprofile", os.path.join(os.path.dirname(__file__), "simple_program.py"))
    assert exitcode == 42
    check_pprof_file(filename + "." + str(pid) + ".1")
    return filename, pid


def test_call_script_pprof_output_interval(tmp_path, monkeypatch):
    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "0.1")
    filename, pid = test_call_script_pprof_output(tmp_path, monkeypatch)
    for i in (2, 3):
        check_pprof_file(filename + "." + str(pid) + (".%d" % i))


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
    check_pprof_file(filename + "." + str(pid) + ".1")
    check_pprof_file(filename + "." + str(child_pid) + ".1")


@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT", False), reason="Not testing gevent")
def test_fork_gevent(monkeypatch):
    monkeypatch.setenv("DD_PROFILING_API_TIMEOUT", "0.1")
    stdout, stderr, exitcode, pid = call_program("python", os.path.join(os.path.dirname(__file__), "gevent_fork.py"))
    assert exitcode == 0
