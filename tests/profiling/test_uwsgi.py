"""Tests for profiler integration with uwsgi.

These tests verify that the Profiler works correctly under various
uwsgi configurations. uwsgi has complex process management (master/worker,
forking, lazy loading) that requires careful handling by the profiler.

Key uwsgi concepts tested:
- Threading: Profiler requires --enable-threads or --threads N
- Master process: Required for multi-worker non-lazy mode (for postfork hooks)
- Lazy apps: Each worker loads app independently (no fork complications)
- Skip-atexit: Required for uwsgi<2.0.30 with lazy mode to avoid crashes

The tests spawn actual uwsgi processes and verify:
1. Invalid configurations are rejected with clear error messages
2. Valid configurations produce actual profile samples in each worker
"""

from importlib.metadata import version
import os
import re
import signal
from subprocess import TimeoutExpired
import sys
import time

import pytest

from tests.contrib.uwsgi import run_uwsgi
from tests.profiling.collector import pprof_utils


# uwsgi is not available on Windows
if sys.platform == "win32":
    pytestmark = pytest.mark.skip

TESTING_GEVENT = os.getenv("DD_PROFILE_TEST_GEVENT", False)
THREADS_MSG = (
    b"ddtrace.internal.uwsgi.uWSGIConfigError: enable-threads option must be set to true, or a positive "
    b"number of threads must be set"
)

uwsgi_app = os.path.join(os.path.dirname(__file__), "uwsgi-app.py")


@pytest.fixture
def uwsgi(monkeypatch, tmp_path):
    # Do not ignore profiler so we have samples in the output pprof
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "0")
    # Do not use pytest tmpdir fixtures which generate directories longer than allowed for a socket file name
    socket_name = str(tmp_path / "uwsgi.sock")
    import os

    cmd = [
        "uwsgi",
        "--need-app",
        "--die-on-term",
        "--socket",
        socket_name,
        "--wsgi-file",
        uwsgi_app,
    ]

    try:
        yield run_uwsgi(cmd)
    finally:
        os.unlink(socket_name)


def test_uwsgi_threads_disabled(uwsgi):
    """Test that profiler fails when uwsgi threads are not enabled.

    The profiler requires threading support to run its background sampling thread.
    Without --enable-threads or --threads N, uwsgi runs in single-threaded mode
    and the profiler cannot function. This test verifies that:
    - The process exits with an error (non-zero exit code)
    - The error message clearly indicates the threading requirement
    """
    proc = uwsgi()
    stdout, _ = proc.communicate()
    assert proc.wait() != 0
    assert THREADS_MSG in stdout


def test_uwsgi_threads_number_set(uwsgi):
    """Test that profiler accepts --threads N as an alternative to --enable-threads.

    uwsgi supports two ways to enable threading:
    - --enable-threads (boolean flag)
    - --threads N (specifies number of threads per worker)

    This test verifies that using --threads 1 satisfies the profiler's threading
    requirement. The process should start successfully without the threading error.
    We use a timeout because on success the process keeps running (serving requests).
    """
    proc = uwsgi("--threads", "1")
    try:
        stdout, _ = proc.communicate(timeout=1)
    except TimeoutExpired:
        proc.terminate()
        stdout, _ = proc.communicate()
    assert THREADS_MSG not in stdout


def test_uwsgi_threads_enabled(uwsgi, tmp_path, monkeypatch):
    """Test that profiler works correctly with a single uwsgi worker.

    This is the simplest valid configuration:
    - --enable-threads: satisfies the threading requirement
    - Single worker (default): no master process or fork hooks needed

    The test verifies that:
    - The worker starts successfully and we can parse its PID from stdout
    - After running for a few seconds, the profiler collects wall-time samples
    - The profile is written to the output file with the worker's PID suffix
    """
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads")
    worker_pids = _get_worker_pids(proc.stdout, 1)
    # Give some time to the process to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 30
    for pid in worker_pids:
        profile = pprof_utils.parse_newest_profile("%s.%d" % (filename, pid))
        samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        assert len(samples) > 0


def test_uwsgi_threads_processes_no_primary(uwsgi, monkeypatch):
    """Test that profiler fails when using multiple processes without --master.

    When uwsgi runs with --processes N (N > 1), it forks worker processes.
    Without --master, there's no master process to coordinate the workers,
    and critically, the postfork hooks (uwsgidecorators.postfork) are not available.

    The profiler needs postfork hooks to restart itself in each worker after fork.
    Without --master, we cannot register these hooks, so this configuration is
    unsupported. The test verifies the profiler rejects this with a clear error.
    """
    proc = uwsgi("--enable-threads", "--processes", "2")
    stdout, _ = proc.communicate()
    assert (
        b"ddtrace.internal.uwsgi.uWSGIConfigError: master option must be enabled when multiple processes are used"
        in stdout
    )


def _get_worker_pids(stdout, num_worker, num_app_started=1):
    """Parse uwsgi stdout to extract worker PIDs.

    Reads lines from uwsgi stdout looking for:
    - "spawned uWSGI worker N ... (pid: PID, ...)" - extracts worker PIDs
    - "WSGI app 0 (mountpoint='') ready" - counts app initializations

    Args:
        stdout: File-like object to read from (proc.stdout)
        num_worker: Expected number of worker processes to find
        num_app_started: Expected number of "app ready" messages
            - For non-lazy mode: 1 (app loaded once in master/first worker)
            - For lazy mode: num_worker (app loaded in each worker)

    Returns when both conditions are met or EOF is reached.
    """
    worker_pids = []
    started = 0
    while True:
        line = stdout.readline()
        if line == b"":
            break
        elif b"WSGI app 0 (mountpoint='') ready" in line:
            started += 1
        else:
            m = re.match(r"^spawned uWSGI worker \d+ .*\(pid: (\d+),", line.decode())
            if m:
                worker_pids.append(int(m.group(1)))

        if len(worker_pids) == num_worker and num_app_started == started:
            break

    return worker_pids


def _wait_for_profile_samples(filename_prefix, pid, value_type, timeout=10.0, interval=0.1):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            profile = pprof_utils.parse_newest_profile(
                "%s.%d" % (filename_prefix, pid),
                assert_samples=False,
                allow_penultimate=True,
            )
        except (IndexError, FileNotFoundError):
            time.sleep(interval)
            continue

        samples = pprof_utils.get_samples_with_value_type(profile, value_type)
        if samples:
            return samples
        time.sleep(interval)

    assert False, "Timed out waiting for %s samples for pid %d" % (value_type, pid)


def test_uwsgi_threads_processes_primary(uwsgi, tmp_path, monkeypatch):
    """Test that profiler works correctly with multiple workers and master process.

    This is the standard production configuration for multi-worker uwsgi:
    - --enable-threads: satisfies the threading requirement
    - --master: enables the master process that manages workers
    - --py-call-uwsgi-fork-hooks: ensures Python fork hooks are called after fork
    - --processes 2: spawns 2 worker processes

    With --master, the profiler can register postfork hooks via uwsgidecorators.postfork()
    to restart the profiler in each worker after fork. The test verifies that:
    - Both workers start successfully
    - Each worker independently collects wall-time samples
    - Profiles are written with each worker's PID suffix
    """
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--master", "--py-call-uwsgi-fork-hooks", "--processes", "2")
    worker_pids = _get_worker_pids(proc.stdout, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 0
    for pid in worker_pids:
        profile = pprof_utils.parse_newest_profile("%s.%d" % (filename, pid))
        samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        assert len(samples) > 0


def test_uwsgi_threads_processes_primary_lazy_apps(uwsgi, tmp_path, monkeypatch):
    """Test profiler with --lazy-apps mode (app loaded independently in each worker).

    With --lazy-apps, each worker loads the application independently after fork,
    rather than the master loading it once and forking. This means:
    - The profiler is initialized fresh in each worker (no fork complications)
    - No postfork hooks are needed since each worker starts the profiler directly
    - Each worker reports "WSGI app ready" independently (num_app_started=2)

    --skip-atexit is required for uwsgi<2.0.30 to avoid crashes on worker exit.
    See https://github.com/unbit/uwsgi/pull/2726

    The test verifies that each worker independently collects profile samples.
    """
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "1")
    # For uwsgi<2.0.30, --skip-atexit is required to avoid crashes when
    # the child process exits.
    proc = uwsgi("--enable-threads", "--master", "--processes", "2", "--lazy-apps", "--skip-atexit")
    worker_pids = _get_worker_pids(proc.stdout, 2, 2)
    # Give some time to child to actually startup and output a profile
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 0
    for pid in worker_pids:
        profile = pprof_utils.parse_newest_profile("%s.%d" % (filename, pid))
        samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        assert len(samples) > 0


def test_uwsgi_threads_processes_no_primary_lazy_apps(uwsgi, tmp_path, monkeypatch):
    """Test profiler with --lazy-apps but WITHOUT --master.

    This is a special case: --lazy-apps without --master. Unlike the non-lazy case,
    this configuration IS supported because:
    - With --lazy-apps, each worker loads the app independently (no fork of initialized state)
    - The profiler starts fresh in each worker, so no postfork hooks are needed
    - Without --master, workers are independent processes (first worker acts as "parent")

    This test verifies that:
    - Both workers start and load the app independently
    - Profile samples are collected from each worker
    - Workers can be terminated cleanly (handling potential zombie processes)

    Note: Without --master, termination is more complex - we manually kill processes
    and handle the case where workers may become zombies.
    """
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "1")
    # For uwsgi<2.0.30, --skip-atexit is required to avoid crashes when
    # the child process exits.
    proc = uwsgi("--enable-threads", "--processes", "2", "--lazy-apps", "--skip-atexit")
    worker_pids = _get_worker_pids(proc.stdout, 2, 2)
    assert len(worker_pids) == 2

    # Give some time to child to actually startup before terminating the master
    time.sleep(3)

    # Kill master process
    parent_pid: int = worker_pids[0]
    os.kill(parent_pid, signal.SIGTERM)

    # Wait for master to exit
    res_pid, res_status = os.waitpid(parent_pid, 0)
    print("")
    print(f"INFO: Master process {parent_pid} exited with status {res_status} and pid {res_pid}")

    # Attempt to kill worker proc once
    worker_pid: int = worker_pids[1]
    print(f"DEBUG: Checking worker {worker_pid} status after master exit:")
    try:
        os.kill(worker_pid, 0)
        print(f"WARNING: Worker {worker_pid} is a zombie (will be cleaned up by init).")

        os.kill(worker_pid, signal.SIGKILL)
        print(f"WARNING: Worker {worker_pid} could not be killed with SIGKILL (will be cleaned up by init).")
    except OSError:
        print(f"INFO: Worker {worker_pid} was successfully killed.")

    for pid in worker_pids:
        _wait_for_profile_samples(filename, pid, "wall-time")


@pytest.mark.parametrize("lazy_flag", ["--lazy-apps", "--lazy"])
@pytest.mark.skipif(
    tuple(int(x) for x in version("uwsgi").split(".")) >= (2, 0, 30),
    reason="uwsgi>=2.0.30 does not require --skip-atexit",
)
def test_uwsgi_require_skip_atexit_when_lazy_with_master(uwsgi, lazy_flag):
    """Test that --skip-atexit warning is shown for lazy mode on uwsgi<2.0.30.

    For uwsgi versions before 2.0.30, using --lazy-apps or --lazy without
    --skip-atexit can cause crashes when workers exit. This is due to a bug
    in uwsgi's atexit handling that was fixed in 2.0.30.
    See: https://github.com/unbit/uwsgi/pull/2726

    The profiler detects this dangerous configuration and emits a deprecation
    warning. This test verifies the warning is shown when:
    - Using --lazy-apps or --lazy
    - With --master (managed worker processes)
    - Without --skip-atexit

    This test is skipped on uwsgi>=2.0.30 where the bug is fixed.
    """
    expected_warning = b"ddtrace.internal.uwsgi.uWSGIConfigDeprecationWarning: skip-atexit option must be set"

    proc = uwsgi("--enable-threads", "--master", "--processes", "2", lazy_flag)
    time.sleep(1)
    proc.terminate()
    stdout, _ = proc.communicate()
    assert expected_warning in stdout


@pytest.mark.parametrize("lazy_flag", ["--lazy-apps", "--lazy"])
@pytest.mark.skipif(
    tuple(int(x) for x in version("uwsgi").split(".")) >= (2, 0, 30),
    reason="uwsgi>=2.0.30 does not require --skip-atexit",
)
def test_uwsgi_require_skip_atexit_when_lazy_without_master(uwsgi, lazy_flag):
    """Test that --skip-atexit warning is shown for lazy mode WITHOUT --master.

    Similar to test_uwsgi_require_skip_atexit_when_lazy_with_master, but tests
    the case where --lazy-apps/--lazy is used without --master. This combination
    is valid (unlike non-lazy without master), but still requires --skip-atexit
    on uwsgi<2.0.30 to avoid crashes.

    Since each worker loads the app independently in lazy mode, the warning
    should be emitted by EACH worker (num_workers times). The test reads stdout
    until we see the expected number of warnings, then terminates the workers.

    This test is skipped on uwsgi>=2.0.30 where the bug is fixed.
    """
    expected_warning = b"ddtrace.internal.uwsgi.uWSGIConfigDeprecationWarning: skip-atexit option must be set"
    num_workers = 2
    proc = uwsgi("--enable-threads", "--processes", str(num_workers), lazy_flag)

    worker_pids = []
    logged_warning = 0
    while True:
        line = proc.stdout.readline()
        if line == b"":
            break
        if expected_warning in line:
            logged_warning += 1
        else:
            m = re.match(r"^spawned uWSGI worker \d+ .*\(pid: (\d+),", line.decode())
            if m:
                worker_pids.append(int(m.group(1)))

        if logged_warning == num_workers:
            break

    for pid in worker_pids:
        os.kill(pid, signal.SIGTERM)
