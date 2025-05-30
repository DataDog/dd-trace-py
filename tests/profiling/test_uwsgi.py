# -*- encoding: utf-8 -*-
import os
import re
import signal
from subprocess import TimeoutExpired
import sys
import tempfile
import time

import pytest

from tests.contrib.uwsgi import run_uwsgi

from . import utils


# uwsgi is not available on Windows
if sys.platform == "win32":
    pytestmark = pytest.mark.skip

THREADS_MSG = (
    b"ddtrace.internal.uwsgi.uWSGIConfigError: enable-threads option must be set to true, or a positive "
    b"number of threads must be set"
)

uwsgi_app = os.path.join(os.path.dirname(__file__), "uwsgi-app.py")


@pytest.fixture
def uwsgi(monkeypatch):
    # Do not ignore profiler so we have samples in the output pprof
    monkeypatch.setenv("DD_PROFILING_IGNORE_PROFILER", "0")
    # Do not use pytest tmpdir fixtures which generate directories longer than allowed for a socket file name
    socket_name = tempfile.mktemp()
    cmd = ["uwsgi", "--need-app", "--die-on-term", "--socket", socket_name, "--wsgi-file", uwsgi_app]

    try:
        yield run_uwsgi(cmd)
    finally:
        os.unlink(socket_name)


def test_uwsgi_threads_disabled(uwsgi):
    proc = uwsgi()
    stdout, _ = proc.communicate()
    assert proc.wait() != 0
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
    assert proc.wait() == 30
    for pid in worker_pids:
        utils.check_pprof_file("%s.%d" % (filename, pid))


def test_uwsgi_threads_processes_no_master(uwsgi, monkeypatch):
    proc = uwsgi("--enable-threads", "--processes", "2")
    stdout, _ = proc.communicate()
    assert (
        b"ddtrace.internal.uwsgi.uWSGIConfigError: master option must be enabled when multiple processes are used"
        in stdout
    )


def _get_worker_pids(stdout, num_worker, num_app_started=1):
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


def test_uwsgi_threads_processes_master(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--master", "--py-call-uwsgi-fork-hooks", "--processes", "2")
    worker_pids = _get_worker_pids(proc.stdout, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 0
    for pid in worker_pids:
        utils.check_pprof_file("%s.%d" % (filename, pid))


def test_uwsgi_threads_processes_master_lazy_apps(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--master", "--processes", "2", "--lazy-apps")
    worker_pids = _get_worker_pids(proc.stdout, 2, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    proc.terminate()
    assert proc.wait() == 0
    for pid in worker_pids:
        utils.check_pprof_file("%s.%d" % (filename, pid))


def test_uwsgi_threads_processes_no_master_lazy_apps(uwsgi, tmp_path, monkeypatch):
    filename = str(tmp_path / "uwsgi.pprof")
    monkeypatch.setenv("DD_PROFILING_OUTPUT_PPROF", filename)
    proc = uwsgi("--enable-threads", "--processes", "2", "--lazy-apps")
    worker_pids = _get_worker_pids(proc.stdout, 2, 2)
    # Give some time to child to actually startup
    time.sleep(3)
    # The processes are started without a master/parent so killing one does not kill the other:
    # Kill them all and wait until they die.
    for pid in worker_pids:
        os.kill(pid, signal.SIGTERM)
    # The first worker is our child, we can wait for it "normally"
    os.waitpid(worker_pids[0], 0)
    # The other ones are grandchildren, we can't wait for it with `waitpid`
    for pid in worker_pids[1:]:
        # Wait for the uwsgi workers to all die
        while True:
            try:
                os.kill(pid, 0)
            except OSError:
                break
    for pid in worker_pids:
        utils.check_pprof_file("%s.%d" % (filename, pid))
