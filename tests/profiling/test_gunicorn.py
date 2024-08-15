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


def _get_worker_pids(stdout):
    # type: (str) -> list[int]
    return [int(_) for _ in re.findall(r"Booting worker with pid: (\d+)", stdout)]


def test_gunicorn_tests_can_work():
    # type: () -> None
    from ddtrace.settings.profiling import config as profiler_config

    assert profiler_config._force_legacy_exporter
    assert not profiler_config.export.libdd_enabled


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

    utils.check_pprof_file("%s.%d.1" % (filename, proc.pid))
    for pid in worker_pids:
        utils.check_pprof_file("%s.%d.1" % (filename, pid))


def test_gunicorn(gunicorn, tmp_path, monkeypatch):
    # type: (...) -> None
    args = ("-k", "gevent") if TESTING_GEVENT else tuple()
    _test_gunicorn(gunicorn, tmp_path, monkeypatch, *args)
