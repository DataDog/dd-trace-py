# -*- encoding: utf-8 -*-
from __future__ import annotations

import os
import pathlib
import re
import subprocess
import sys
import threading
import time
from typing import Any
from typing import Callable
from typing import Generator
import urllib.request

import pytest
from typing_extensions import TypeAlias

from tests.profiling.collector import pprof_utils


# DEV: gunicorn tests are hard to debug, so keeping these print statements for
# future debugging
DEBUG_PRINT = True


def debug_print(*args: Any) -> None:
    if DEBUG_PRINT:
        print(*args)


# gunicorn is not available on Windows
if sys.platform == "win32":
    pytestmark = pytest.mark.skip

TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)

RunGunicornFunc: TypeAlias = Callable[..., subprocess.Popen[bytes]]


def _run_gunicorn(*args: str) -> subprocess.Popen[bytes]:
    cmd = (
        [
            "ddtrace-run",
            "gunicorn",
            "--bind",
            "127.0.0.1:7644",
            "--worker-tmp-dir",
            "/dev/shm",
            "-c",
            os.path.dirname(__file__) + "/gunicorn.conf.py",
            "--chdir",
            os.path.dirname(__file__),
        ]
        + list(args)
        + ["tests.profiling.gunicorn-app:app"]
    )
    return subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)


@pytest.fixture
def gunicorn(monkeypatch: pytest.MonkeyPatch) -> Generator[RunGunicornFunc, None, None]:
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "1")
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")

    yield _run_gunicorn


def _get_worker_pids(stdout: str) -> list[int]:
    return [int(_) for _ in re.findall(r"Booting worker with pid: (\d+)", stdout)]


def _test_gunicorn(
    gunicorn: RunGunicornFunc,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
    *args: str,
) -> None:
    filename = str(tmp_path / "gunicorn.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED", "0")

    debug_print("Creating gunicorn workers")
    # DEV: We only start 1 worker to simplify the test
    proc = gunicorn("-w", "1", *args)
    # Wait for the workers to start
    time.sleep(5)

    if proc.poll() is not None:
        pytest.fail("Gunicorn failed to start")

    debug_print("Making request to gunicorn server")
    try:
        with urllib.request.urlopen("http://127.0.0.1:7644", timeout=5) as f:
            status_code = f.getcode()
            assert status_code == 200, status_code
            response = f.read().decode()
            debug_print(response)
    except Exception as e:
        proc.terminate()
        assert proc.stdout is not None
        output = proc.stdout.read().decode()
        print(output)
        pytest.fail(f"Failed to make request to gunicorn server {e}")
    finally:
        # Need to terminate the process to get the output and release the port
        proc.terminate()

    debug_print("Reading gunicorn worker output to get PIDs")
    assert proc.stdout is not None
    output = proc.stdout.read().decode()
    worker_pids = _get_worker_pids(output)
    debug_print(f"Gunicorn worker PIDs: {worker_pids}")

    for line in output.splitlines():
        debug_print(line)

    assert len(worker_pids) == 1, output

    debug_print("Waiting for gunicorn process to terminate")
    try:
        assert proc.wait(timeout=5) == 0, output
    except subprocess.TimeoutExpired:
        pytest.fail(f"Failed to terminate gunicorn process: {output}")
    assert "module 'threading' has no attribute '_active'" not in output, output

    for pid in worker_pids:
        debug_print(f"Reading pprof file with prefix {filename}.{pid}")
        profile = pprof_utils.parse_newest_profile(f"{filename}.{pid}", allow_penultimate=True)
        # This returns a list of samples that have non-zero cpu-time
        samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
        assert len(samples) > 0

        # DEV: somehow the filename is reported as either __init__.py or gunicorn-app.py
        # when run on GitLab CI. We need to match either of these two.
        filename_regex = r"^(?:__init__\.py|gunicorn-app\.py)$"

        expected_location = pprof_utils.StackLocation(function_name="fib", filename=filename_regex, line_no=12)

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            # DEV: we expect multiple locations as fibonacci is recursive
            expected_sample=pprof_utils.StackEvent(locations=[expected_location, expected_location]),
        )


def test_gunicorn(
    gunicorn: RunGunicornFunc,
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    args: tuple[str, ...] = ("-k", "gevent") if TESTING_GEVENT else ()
    _test_gunicorn(gunicorn, tmp_path, monkeypatch, *args)


@pytest.mark.skipif(not TESTING_GEVENT, reason="gevent is not available")
@pytest.mark.skipif(sys.platform == "win32", reason="SIGTERM not available on Windows")
def test_gunicorn_gevent_sigterm_graceful_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify in-flight requests complete after SIGTERM with gevent workers and profiling enabled.

    Regression test: the greenlet_tracer in ddtrace.profiling._gevent used to mutate
    module-level state (set.add) during greenlet switches.  When the switch carried a
    SystemExit (raised by gunicorn's Worker.handle_exit on SIGTERM), this mutation
    disrupted gevent's exception propagation and caused the worker to die immediately
    instead of draining in-flight requests.
    """
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "1")

    port = 7645
    cmd = [
        "ddtrace-run",
        "gunicorn",
        "--bind",
        f"127.0.0.1:{port}",
        "--worker-tmp-dir",
        "/dev/shm",
        "-k",
        "gevent",
        "-w",
        "1",
        "--graceful-timeout",
        "10",
        "--chdir",
        os.path.dirname(__file__),
        "tests.profiling.gunicorn_sigterm_app:app",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        time.sleep(5)
        if proc.poll() is not None:
            assert proc.stdout is not None
            output = proc.stdout.read().decode()
            pytest.fail(f"gunicorn failed to start:\n{output}")

        # Verify the server is ready
        debug_print("Checking gunicorn is ready")
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/health", timeout=3) as f:
                assert f.getcode() == 200
        except Exception as e:
            proc.terminate()
            assert proc.stdout is not None
            output = proc.stdout.read().decode()
            pytest.fail(f"gunicorn not ready: {e}\n{output}")

        # Fire a slow request in a background thread so we can send SIGTERM
        # while it is still in-flight.
        result: dict[str, Any] = {}

        def _make_slow_request() -> None:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/slow", timeout=15) as f:
                    result["status"] = f.getcode()
                    result["body"] = f.read().decode()
            except Exception as exc:
                result["error"] = exc

        t = threading.Thread(target=_make_slow_request)
        t.start()

        # Give the request time to reach the worker
        time.sleep(1)

        debug_print("Sending SIGTERM to gunicorn master")
        proc.terminate()

        # Wait for the slow request to complete
        t.join(timeout=15)

        debug_print(f"Request result: {result}")

        assert "error" not in result, f"In-flight request failed during shutdown: {result.get('error')}"
        assert result.get("status") == 200, f"Expected 200, got {result}"
        assert "slow-ok" in result.get("body", ""), f"Unexpected response body: {result}"

        # Gunicorn master should exit cleanly
        try:
            exit_code = proc.wait(timeout=10)
            debug_print(f"gunicorn exit code: {exit_code}")
        except subprocess.TimeoutExpired:
            proc.kill()
            pytest.fail("gunicorn did not exit within timeout after SIGTERM")
    finally:
        if proc.poll() is None:
            proc.kill()
        assert proc.stdout is not None
        output = proc.stdout.read().decode()
        debug_print("=== gunicorn output ===")
        for line in output.splitlines():
            debug_print(line)
