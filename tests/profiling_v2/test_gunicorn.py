# -*- encoding: utf-8 -*-
import os
import re
import subprocess
import sys
import time

import pytest

from tests.profiling.collector import pprof_utils


# gunicorn is not available on Windows
if sys.platform == "win32":
    pytestmark = pytest.mark.skip

TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


def _run_gunicorn(*args):
    cmd = (
        ["ddtrace-run", "gunicorn", "--bind", "127.0.0.1:7643", "--chdir", os.path.dirname(__file__)]
        + list(args)
        + ["tests.profiling.gunicorn-app:app"]
    )
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


@pytest.fixture
def gunicorn(monkeypatch):
    # Do not ignore profiler so we have samples in the output pprof
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "0")
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")
    # This was needed for the gunicorn process to start and print worker startup
    # messages. Without this, the test can't find the worker PIDs.
    monkeypatch.setenv("DD_PROFILING_STACK_V2_ENABLED", "1")

    yield _run_gunicorn


def _get_worker_pids(stdout):
    # type: (str) -> list[int]
    return [int(_) for _ in re.findall(r"Booting worker with pid: (\d+)", stdout)]


def _test_gunicorn(gunicorn, tmp_path, monkeypatch, *args):
    # type: (...) -> None
    filename = str(tmp_path / "gunicorn.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)

    proc = gunicorn("-w", "3", *args)
    time.sleep(3)
    proc.terminate()
    output = proc.stdout.read().decode()
    worker_pids = _get_worker_pids(output)

    assert len(worker_pids) == 3, output
    assert proc.wait() == 0, output
    assert "module 'threading' has no attribute '_active'" not in output, output

    profile = pprof_utils.parse_profile("%s.%d" % (filename, proc.pid))
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0


def test_gunicorn(gunicorn, tmp_path, monkeypatch):
    # type: (...) -> None
    args = ("-k", "gevent") if TESTING_GEVENT else tuple()
    _test_gunicorn(gunicorn, tmp_path, monkeypatch, *args)
