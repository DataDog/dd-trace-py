# -*- encoding: utf-8 -*-
from __future__ import annotations

import concurrent.futures
import os
import pathlib
import re
import signal
import subprocess
import sys
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


def test_gunicorn_gevent_sigterm_graceful_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for SCP-1077.

    When profiling is enabled with a gevent worker, sending SIGTERM to gunicorn
    should allow in-flight requests to complete (graceful shutdown) rather than
    killing them immediately.
    """
    pytest.importorskip("gevent")

    bind_addr = "127.0.0.1:7645"
    cmd = [
        "ddtrace-run",
        "gunicorn",
        "--bind",
        bind_addr,
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
        "gunicorn_sigterm_app:app",
    ]

    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        # Wait for the server to be ready
        ready = False
        for _ in range(30):
            time.sleep(1)
            if proc.poll() is not None:
                assert proc.stdout is not None
                output = proc.stdout.read().decode()
                pytest.fail(f"Gunicorn exited early:\n{output}")
            try:
                with urllib.request.urlopen(f"http://{bind_addr}/health", timeout=2) as resp:
                    if resp.getcode() == 200:
                        ready = True
                        break
            except Exception:
                continue

        if not ready:
            pytest.fail("Gunicorn server never became ready")

        debug_print("Server is ready, firing slow request")

        # Fire the slow request in a background thread
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            slow_future = pool.submit(
                urllib.request.urlopen,
                f"http://{bind_addr}/slow",
                timeout=30,
            )

            # Give the request time to reach the worker
            time.sleep(1)

            debug_print("Sending SIGTERM to gunicorn")
            proc.send_signal(signal.SIGTERM)

            # The slow request should complete successfully despite SIGTERM
            try:
                resp = slow_future.result(timeout=30)
                status_code = resp.getcode()
                body = resp.read().decode()
                resp.close()
            except Exception as e:
                assert proc.stdout is not None
                output = proc.stdout.read().decode()
                pytest.fail(f"Slow request failed after SIGTERM: {e}\nGunicorn output:\n{output}")

        debug_print(f"Slow request returned: status={status_code}, body={body!r}")
        assert status_code == 200, f"Expected 200, got {status_code}"
        assert body == "slow-ok", f"Expected 'slow-ok', got {body!r}"

        # Gunicorn should exit cleanly
        try:
            exit_code = proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            assert proc.stdout is not None
            output = proc.stdout.read().decode()
            pytest.fail(f"Gunicorn did not exit after SIGTERM:\n{output}")

        assert proc.stdout is not None
        output = proc.stdout.read().decode()
        debug_print(f"Gunicorn exit code: {exit_code}")
        for line in output.splitlines():
            debug_print(line)

        assert exit_code == 0, f"Gunicorn exited with code {exit_code}:\n{output}"

    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()
