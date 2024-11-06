# -*- encoding: utf-8 -*-
import os
import re
import subprocess
import sys
import time
import urllib.request

import pytest

from tests.profiling.collector import pprof_utils


# DEV: gunicorn tests are hard to debug, so keeping these print statements for
# future debugging
DEBUG_PRINT = False


def debug_print(*args):
    if DEBUG_PRINT:
        print(*args)


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

    # DEV: We only start 1 worker to simplify the test
    proc = gunicorn("-w", "1", *args)
    # Wait for the workers to start
    time.sleep(3)

    try:
        with urllib.request.urlopen("http://127.0.0.1:7643") as f:
            status_code = f.getcode()
            assert status_code == 200, status_code
            response = f.read().decode()
            debug_print(response)

    except Exception as e:
        pytest.fail("Failed to make request to gunicorn server %s" % e)
    finally:
        # Need to terminate the process to get the output and release the port
        proc.terminate()

    output = proc.stdout.read().decode()
    worker_pids = _get_worker_pids(output)

    for line in output.splitlines():
        debug_print(line)

    assert len(worker_pids) == 1, output
    assert proc.wait() == 0, output
    assert "module 'threading' has no attribute '_active'" not in output, output

    for pid in worker_pids:
        debug_print("Reading pprof file with prefix %s.%d" % (filename, pid))
        profile = pprof_utils.parse_profile("%s.%d" % (filename, pid))
        # This returns a list of samples that have non-zero cpu-time
        samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
        assert len(samples) > 0

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                locations=[
                    pprof_utils.StackLocation(function_name="fib", filename="gunicorn-app.py", line_no=8),
                    pprof_utils.StackLocation(function_name="fib", filename="gunicorn-app.py", line_no=8),
                ]
            ),
        )


def test_gunicorn(gunicorn, tmp_path, monkeypatch):
    # type: (...) -> None
    args = ("-k", "gevent") if TESTING_GEVENT else tuple()
    _test_gunicorn(gunicorn, tmp_path, monkeypatch, *args)
