"""Smoke test: with --py-call-uwsgi-fork-hooks, uwsgi drives CPython's
os.register_at_fork machinery around every worker fork, so ddtrace's general
fork-safety registry (ddtrace.internal.forksafe) should reinitialize itself in
each worker -- not just the profiler-specific PeriodicThread restart already
covered by tests/profiling/test_uwsgi.py.

Each worker's app module registers an on_runtime_id_change callback that fires
synchronously inside the forking thread right after the raw C-level fork --
the same point ddtrace's own forksafe registry runs -- and there creates and
finishes a span, proving the tracer itself still works post-fork.
"""

import os
import re
import subprocess
import sys
import time

import pytest

from tests.contrib.uwsgi import run_uwsgi


if sys.platform == "win32":
    pytestmark = pytest.mark.skip

uwsgi_app = os.path.join(os.path.dirname(__file__), "uwsgi-fork-smoke-app.py")

NUM_WORKERS = 2
STARTUP_TIMEOUT = 10


def _base_cmd(socket_name):
    return [
        "uwsgi",
        "--wsgi-file",
        uwsgi_app,
        "--master",
        "--enable-threads",
        "--py-call-uwsgi-fork-hooks",
        "--processes",
        str(NUM_WORKERS),
        "--import",
        "ddtrace.auto",
        "--die-on-term",
        "--socket",
        socket_name,
    ]


def _terminate(proc):
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=STARTUP_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _get_worker_pids(stdout, num_workers):
    # Non-lazy mode loads the app once, before any worker is forked, so "WSGI
    # app 0 ready" is only ever printed once regardless of num_workers.
    deadline = time.monotonic() + STARTUP_TIMEOUT
    pids = []
    ready = 0
    while time.monotonic() < deadline and (len(pids) < num_workers or ready < 1):
        line = stdout.readline()
        if not line:
            break
        m = re.match(rb"^spawned uWSGI worker \d+ .*\(pid: (\d+),", line)
        if m:
            pids.append(int(m.group(1)))
        if re.search(rb"WSGI app 0 \(mountpoint=''\) ready", line):
            ready += 1
    assert len(pids) == num_workers, "expected %d workers, saw %r" % (num_workers, pids)
    assert ready == 1, "expected app to be loaded once, saw %d" % ready
    return pids


@pytest.fixture
def uwsgi_fork_hooks_app(tmp_path):
    # Do not use pytest tmpdir fixtures which generate directories longer than allowed for a socket file name
    socket_name = str(tmp_path / "uwsgi.sock")

    started = []

    def _run(*extra_args):
        proc = run_uwsgi(_base_cmd(socket_name))(*extra_args)
        started.append(proc)
        return proc

    yield _run

    for proc in started:
        _terminate(proc)
    if os.path.exists(socket_name):
        os.unlink(socket_name)


def test_tracer_survives_fork_without_lazy_apps(uwsgi_fork_hooks_app, tmp_path, monkeypatch):
    monkeypatch.setenv("DD_TEST_FORK_SMOKE_OUTPUT_DIR", str(tmp_path))
    proc = uwsgi_fork_hooks_app()

    worker_pids = _get_worker_pids(proc.stdout, NUM_WORKERS)

    for pid in worker_pids:
        log_path = tmp_path / ("worker-%d.log" % pid)
        deadline = time.monotonic() + STARTUP_TIMEOUT
        while not log_path.exists() and time.monotonic() < deadline:
            time.sleep(0.1)
        assert log_path.exists(), "no fork-safety log written for worker %d" % pid

        content = log_path.read_text()
        assert "error" not in content, content
        assert re.search(r"^changed \S+ True$", content, re.M), content
