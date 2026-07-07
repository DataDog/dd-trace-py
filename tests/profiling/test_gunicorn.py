# -*- encoding: utf-8 -*-
from __future__ import annotations

import concurrent.futures
import os
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
from tests.profiling.collector.pprof_utils import parse_pid_from_request
from tests.profiling.utils import get_all_requests_from_agent


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

# Use /dev/shm for Linux; fall back to /tmp on macOS and other platforms.
_WORKER_TMP_DIR = "/dev/shm" if os.path.isdir("/dev/shm") else "/tmp"


def _run_gunicorn(*args: str, app: str = "tests.profiling.gunicorn-app:app") -> subprocess.Popen[bytes]:
    cmd = (
        [
            "ddtrace-run",
            "gunicorn",
            "--bind",
            "127.0.0.1:7644",
            "--worker-tmp-dir",
            _WORKER_TMP_DIR,
            "-c",
            os.path.dirname(__file__) + "/gunicorn.conf.py",
            "--chdir",
            os.path.dirname(__file__),
        ]
        + list(args)
        + [app]
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
    monkeypatch: pytest.MonkeyPatch,
    agent_client,
    *args: str,
) -> None:
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

    debug_print("Retrieving profiles from capture server")
    # Multiple uploads may arrive (periodic scheduler + shutdown flush + master process).
    # Search all of them for the worker profile that has cpu-time samples with fib.
    reqs = get_all_requests_from_agent(agent_client)
    profiles = [pprof_utils.parse_profile_from_request(r) for r in reqs]

    # DEV: somehow the filename is reported as either __init__.py or gunicorn-app.py
    # when run on GitLab CI. We need to match either of these two.
    filename_regex = r"^(?:__init__\.py|gunicorn-app\.py)$"
    expected_location = pprof_utils.StackLocation(function_name="fib", filename=filename_regex, line_no=9)

    found = False
    for profile in profiles:
        samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
        if not samples:
            continue
        try:
            pprof_utils.assert_profile_has_sample(
                profile,
                samples=samples,
                # DEV: we expect multiple locations as fibonacci is recursive
                expected_sample=pprof_utils.StackEvent(locations=[expected_location, expected_location]),
            )
            found = True
            break
        except AssertionError:
            continue
    assert found, (
        f"Expected fib samples not found in any of {len(profiles)} profile upload(s). "
        "Worker may not have been profiled during the request."
    )


def test_gunicorn(
    gunicorn: RunGunicornFunc,
    monkeypatch: pytest.MonkeyPatch,
    profiling_test_agent,
) -> None:
    args: tuple[str, ...] = ("-k", "gevent") if TESTING_GEVENT else ()
    _test_gunicorn(gunicorn, monkeypatch, profiling_test_agent, *args)


def _run_sigterm_graceful_shutdown_test(
    cmd: list[str],
    bind_addr: str,
    label: str,
) -> None:
    """Shared logic for SIGTERM graceful-shutdown tests.

    Starts gunicorn with the given cmd, waits for readiness, fires a slow
    request, sends SIGTERM, and asserts the slow request completes.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        # Wait for the server to be ready
        ready = False
        for _ in range(30):
            time.sleep(1)
            if proc.poll() is not None:
                assert proc.stdout is not None
                output = proc.stdout.read().decode()
                pytest.fail(f"[{label}] Gunicorn exited early:\n{output}")
            try:
                with urllib.request.urlopen(f"http://{bind_addr}/health", timeout=2) as resp:
                    if resp.getcode() == 200:
                        ready = True
                        break
            except Exception:
                continue

        if not ready:
            pytest.fail(f"[{label}] Gunicorn server never became ready")

        debug_print(f"[{label}] Firing slow request")

        # Fire the slow request in a background thread
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            slow_future = pool.submit(
                urllib.request.urlopen,
                f"http://{bind_addr}/slow",
                timeout=30,
            )

            # Give the request time to reach the worker
            time.sleep(1)

            debug_print(f"[{label}] Sending SIGTERM to gunicorn master (pid={proc.pid})")
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
                pytest.fail(f"[{label}] Slow request failed after SIGTERM: {e}\nGunicorn output:\n{output}")

        debug_print(f"[{label}] Slow request returned: status={status_code}, body={body!r}")
        assert status_code == 200, f"[{label}] Expected 200, got {status_code}"
        assert body == "slow-ok", f"[{label}] Expected 'slow-ok', got {body!r}"

        # Gunicorn should exit cleanly
        try:
            exit_code = proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
            assert proc.stdout is not None
            output = proc.stdout.read().decode()
            pytest.fail(f"[{label}] Gunicorn did not exit after SIGTERM:\n{output}")

        assert proc.stdout is not None
        output = proc.stdout.read().decode()
        debug_print(f"[{label}] Gunicorn exit code: {exit_code}")
        for line in output.splitlines():
            debug_print(line)

        assert exit_code == 0, f"[{label}] Gunicorn exited with code {exit_code}:\n{output}"

    finally:
        if proc.poll() is None:
            proc.kill()
            proc.wait()


def test_gunicorn_gevent_sigterm_graceful_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression test for SCP-1077.

    When profiling is enabled with a gevent worker, sending SIGTERM to gunicorn
    should allow in-flight requests to complete (graceful shutdown) rather than
    killing them immediately.
    """
    pytest.importorskip("gevent")

    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")

    bind_addr = "127.0.0.1:7645"
    cmd = [
        "ddtrace-run",
        "gunicorn",
        "--bind",
        bind_addr,
        "--worker-tmp-dir",
        _WORKER_TMP_DIR,
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

    _run_sigterm_graceful_shutdown_test(cmd, bind_addr, label="ddtrace")


def test_gunicorn_profile_export_count_two_workers(
    gunicorn: RunGunicornFunc,
    monkeypatch: pytest.MonkeyPatch,
    profiling_test_agent,
) -> None:
    monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED", "0")

    args: tuple[str, ...] = ("-k", "gevent") if TESTING_GEVENT else ()

    agent_client = profiling_test_agent
    proc = gunicorn("-w", "2", *args, app="tests.profiling.gunicorn_count_app:app")
    time.sleep(5)

    if proc.poll() is not None:
        assert proc.stdout is not None
        output = proc.stdout.read().decode()
        pytest.fail(f"Gunicorn failed to start: {output}")

    try:
        for _ in range(4):
            with urllib.request.urlopen("http://127.0.0.1:7644", timeout=5) as f:
                assert f.getcode() == 200
    except Exception as e:
        proc.terminate()
        assert proc.stdout is not None
        output = proc.stdout.read().decode()
        pytest.fail(f"Failed to make request to gunicorn server {e}: {output}")
    finally:
        proc.terminate()

    assert proc.stdout is not None
    output = proc.stdout.read().decode()
    worker_pids = _get_worker_pids(output)
    assert len(worker_pids) == 2, output

    try:
        assert proc.wait(timeout=5) == 0, output
    except subprocess.TimeoutExpired:
        pytest.fail(f"Failed to terminate gunicorn process: {output}")

    # Expect one upload per worker; poll until both worker PIDs have uploaded.
    reqs = get_all_requests_from_agent(agent_client, min_count=2)
    upload_pids = {parse_pid_from_request(req) for req in reqs}
    for wpid in worker_pids:
        assert wpid in upload_pids, (
            f"Worker PID {wpid} did not upload a profile. Worker PIDs: {worker_pids}, uploaded PIDs: {upload_pids}"
        )


def test_gunicorn_profile_export_count_two_workers_flush_false(
    gunicorn: RunGunicornFunc,
    monkeypatch: pytest.MonkeyPatch,
    profiling_test_agent,
) -> None:
    monkeypatch.setenv("_DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED", "0")

    args: tuple[str, ...] = ("-k", "gevent") if TESTING_GEVENT else ()

    agent_client = profiling_test_agent
    proc = subprocess.Popen(
        [
            "ddtrace-run",
            "gunicorn",
            "--bind",
            "127.0.0.1:7644",
            "--worker-tmp-dir",
            _WORKER_TMP_DIR,
            "-c",
            os.path.dirname(__file__) + "/gunicorn.conf.py",
            "--chdir",
            os.path.dirname(__file__),
            "-w",
            "2",
            *args,
            "tests.profiling.gunicorn_count_app:app",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )

    time.sleep(5)
    if proc.poll() is not None:
        assert proc.stdout is not None
        output = proc.stdout.read().decode()
        pytest.fail(f"Gunicorn failed to start: {output}")

    try:
        with urllib.request.urlopen("http://127.0.0.1:7644", timeout=5) as f:
            assert f.getcode() == 200
    except Exception as e:
        os.killpg(proc.pid, signal.SIGKILL)
        assert proc.stdout is not None
        output = proc.stdout.read().decode()
        pytest.fail(f"Failed to make request to gunicorn server {e}: {output}")

    # Clear any periodic uploads that happened before we issue SIGKILL.
    agent_client.clear()

    os.killpg(proc.pid, signal.SIGKILL)
    assert proc.stdout is not None
    output = proc.stdout.read().decode()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pytest.fail(f"Failed to kill gunicorn process group: {output}")

    # After SIGKILL (no graceful shutdown), no additional profiling data
    # should be uploaded — SIGKILL doesn't allow atexit/shutdown flushes.
    import time as _time

    _time.sleep(1)  # give any in-flight uploads a moment to arrive
    profiles = agent_client.profiling_requests()
    assert profiles == [], f"Expected no profiling uploads after SIGKILL, got {len(profiles)}"
