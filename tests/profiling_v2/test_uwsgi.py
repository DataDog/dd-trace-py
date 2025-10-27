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
    "ddtrace.internal.uwsgi.uWSGIConfigError: enable-threads option must be set to true, or a positive "
    "number of threads must be set"
)

uwsgi_app = os.path.join(os.path.dirname(__file__), "..", "profiling", "uwsgi-app.py")


@pytest.fixture
def uwsgi(monkeypatch, tmp_path):
    # Do not ignore profiler so we have samples in the output pprof
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "0")
    monkeypatch.setenv("DD_PROFILING_ENABLED", "1")
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
        "--import",
        "ddtrace.auto",
    ]

    try:
        yield run_uwsgi(cmd)
    finally:
        os.unlink(socket_name)


def test_uwsgi_threads_disabled(uwsgi):
    proc = uwsgi()
    stdout, _ = proc.communicate()
    proc.wait()
    assert THREADS_MSG in stdout


def test_uwsgi_threads_number_set(uwsgi):
    proc = uwsgi("--threads", "1")
    try:
        stdout, _ = proc.communicate(timeout=1)
    except TimeoutExpired:
        proc.terminate()
        stdout, _ = proc.communicate()
    assert THREADS_MSG not in stdout


def test_uwsgi_threads_enabled(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads")
    worker_pids = _get_worker_pids(proc.stdout, 1)
    # Give some time to the process to actually startup
    time.sleep(3)
    proc.terminate()
    proc.wait()
    for pid in worker_pids:
        profile = pprof_utils.parse_newest_profile("%s.%d" % (filename, pid))
        samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        assert len(samples) > 0


def test_uwsgi_threads_processes_no_primary(uwsgi, monkeypatch):
    proc = uwsgi("--enable-threads", "--processes", "2")
    stdout, _ = proc.communicate()
    assert (
        "ddtrace.internal.uwsgi.uWSGIConfigError: master option must be enabled when multiple processes are used"
        in stdout
    )


def _get_worker_pids(stdout, num_worker, num_app_started=1):
    worker_pids = []
    started = 0
    while True:
        line = stdout.readline().strip()
        if line == "":
            break
        elif "WSGI app 0 (mountpoint='') ready" in line:
            started += 1
        else:
            m = re.match(r"^spawned uWSGI worker \d+ .*\(pid: (\d+),", line)
            if m:
                worker_pids.append(int(m.group(1)))

        if len(worker_pids) == num_worker and num_app_started == started:
            break

    return worker_pids


def test_uwsgi_threads_processes_primary(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--master", "--py-call-uwsgi-fork-hooks", "--processes", "2")
    worker_pids = _get_worker_pids(proc.stdout, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    proc.terminate()
    proc.wait()
    for pid in worker_pids:
        profile = pprof_utils.parse_newest_profile("%s.%d" % (filename, pid))
        samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        assert len(samples) > 0


def test_uwsgi_threads_processes_primary_lazy_apps(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "1")
    monkeypatch.setenv("__DD_TEST_DONT_RAISE", "1")
    # For uwsgi<2.0.30, --skip-atexit is required to avoid crashes when
    # the child process exits.
    proc = uwsgi("--enable-threads", "--master", "--processes", "2", "--lazy-apps", "--skip-atexit")
    worker_pids = _get_worker_pids(proc.stdout, 2, 2)
    # Give some time to child to actually startup and output a profile
    time.sleep(3)
    proc.terminate()
    proc.wait()
    for pid in worker_pids:
        profile = pprof_utils.parse_newest_profile("%s.%d" % (filename, pid))
        samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
        assert len(samples) > 0


def test_uwsgi_threads_processes_no_primary_lazy_apps(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    monkeypatch.setenv("DD_PROFILING_UPLOAD_INTERVAL", "1")
    monkeypatch.setenv("__DD_TEST_DONT_RAISE", "1")
    # For uwsgi<2.0.30, --skip-atexit is required to avoid crashes when
    # the child process exits.
    proc = uwsgi("--enable-threads", "--processes", "2", "--lazy-apps", "--skip-atexit")
    worker_pids = _get_worker_pids(proc.stdout, 2, 2)
    assert len(worker_pids) == 2

    # Give some time to child to actually startup and output a profile
    time.sleep(3)

    # Send Ctrl+C to the process group
    os.killpg(os.getpgid(proc.pid), signal.SIGINT)
    proc.kill()
    proc.wait()

    for pid in worker_pids:
        assert sum(
            len(pprof_utils.get_samples_with_value_type(profile, "wall-time"))
            for profile in pprof_utils.list_profiles("%s.%d" % (filename, pid))
        )


@pytest.mark.parametrize("lazy_flag", ["--lazy-apps", "--lazy"])
@pytest.mark.skipif(
    tuple(int(x) for x in version("uwsgi").split(".")) >= (2, 0, 30),
    reason="uwsgi>=2.0.30 does not require --skip-atexit",
)
def test_uwsgi_require_skip_atexit_when_lazy_with_master(uwsgi, lazy_flag):
    expected_warning = "ddtrace.internal.uwsgi.uWSGIConfigDeprecationWarning: skip-atexit option must be set"

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
    expected_warning = "ddtrace.internal.uwsgi.uWSGIConfigDeprecationWarning: skip-atexit option must be set"
    num_workers = 2
    proc = uwsgi("--enable-threads", "--processes", str(num_workers), lazy_flag)

    worker_pids = []
    logged_warning = 0
    while True:
        line = proc.stdout.readline()
        if line == "":
            break
        if expected_warning in line:
            logged_warning += 1
        else:
            m = re.match(r"^spawned uWSGI worker \d+ .*\(pid: (\d+),", line)
            if m:
                worker_pids.append(int(m.group(1)))

        if logged_warning == num_workers:
            break

    for pid in worker_pids:
        os.kill(pid, signal.SIGTERM)
