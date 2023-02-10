# -*- encoding: utf-8 -*-
import os
import re
import subprocess
import sys
import time

import pytest

from . import utils


# gunicorn is not available on Windows
if sys.platform == "win32":
    pytestmark = pytest.mark.skip

TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)


def _run_gunicorn(*args):
    cmd = (
        ["ddtrace-run", "gunicorn", "--bind", "127.0.0.1:7643", "--chdir", os.path.dirname(__file__)]
        + list(args)
        + ["gunicorn-app:app"]
    )
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


@pytest.fixture
def gunicorn(monkeypatch):
    # Do not ignore profiler so we have samples in the output pprof
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "0")
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")

    yield _run_gunicorn


def _get_worker_pids(stdout, num_worker, num_app_started=1):
    # type: (...) -> list[int]
    worker_pids = []

    while len(worker_pids) != num_worker:
        line = stdout.readline()
        if line == b"":
            raise RuntimeError("Boot failure")

        m = re.search(r"Booting worker with pid: (\d+)", line.decode())
        if m:
            worker_pids.append(int(m.group(1)))

    return worker_pids


def _test_gunicorn(gunicorn, tmp_path, monkeypatch, *args):
    # type: (...) -> None
    filename = str(tmp_path / "gunicorn.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = gunicorn(*args)
    worker_pids = _get_worker_pids(proc.stdout, 1)
    assert len(worker_pids) == 1
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 0
    utils.check_pprof_file("%s.%d.1" % (filename, proc.pid))
    for pid in worker_pids:
        utils.check_pprof_file("%s.%d.1" % (filename, pid))


@pytest.mark.skipif(TESTING_GEVENT, reason="Only testing gevent worker class")
def test_gunicorn(gunicorn, tmp_path, monkeypatch):
    # type: (...) -> None
    _test_gunicorn(gunicorn, tmp_path, monkeypatch)


# This test does not work when run run via pytest:
# [CRITICAL] WORKER TIMEOUT (pid:33923)
# [WARNING] Worker with pid 33923 was terminated due to signal 6
# There's something odd going on with gevent which prevents this from working.
@pytest.mark.skipif(not TESTING_GEVENT, reason="Not testing gevent")
def test_gunicorn_gevent(gunicorn, tmp_path, monkeypatch):
    # type: (...) -> None
    monkeypatch.setenv("DD_GEVENT_PATCH_ALL", "1")
    _test_gunicorn(gunicorn, tmp_path, monkeypatch, "--worker-class", "gevent")
