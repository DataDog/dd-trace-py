# -*- encoding: utf-8 -*-
import os
import re
import subprocess
import sys
import time

import pytest
import requests

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
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "1")
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
    print(filename)

    proc = gunicorn("-w", "1", *args)

    time.sleep(3)

    response = None
    try:
        response = requests.get("http://127.0.0.1:7643")
    except Exception as e:
        print(e)
    finally:
        proc.terminate()

    if response:
        print(response.status_code, response.text)

    output = proc.stdout.read().decode()
    worker_pids = _get_worker_pids(output)

    print(worker_pids)
    for line in output.splitlines():
        print(line)

    assert len(worker_pids) == 1, output
    assert proc.wait() == 0, output
    assert "module 'threading' has no attribute '_active'" not in output, output

    for pid in worker_pids:
        print("%s.%d" % (filename, pid))
        profile = pprof_utils.parse_profile("%s.%d" % (filename, pid))
        samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
        assert len(samples) > 0

        pprof_utils.assert_has_samples(
            profile,
            expected_samples=pprof_utils.StackEvent(
                locations=[pprof_utils.StackLocation(function_name="fib", filename="gunicorn-app.py", line_no=8)]
            ),
        )


def test_gunicorn(gunicorn, tmp_path, monkeypatch):
    # type: (...) -> None
    args = ("-k", "gevent") if TESTING_GEVENT else tuple()
    _test_gunicorn(gunicorn, tmp_path, monkeypatch, *args)
