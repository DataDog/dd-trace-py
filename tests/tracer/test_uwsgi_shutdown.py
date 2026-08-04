"""Regression test: NativeRuntime.shutdown() panics with a Tokio
context-thread-local-destroyed error when a uWSGI worker exits under
`--lazy-apps`/`--die-on-term`. See https://github.com/DataDog/libdatadog/pull/2169.

Only reproduces via a real uWSGI worker exit, not a bare process exit or
self-sent SIGTERM (which bypasses atexit). `--skip-atexit` is a known
mitigation, covered by the second test below.

test_uwsgi_worker_sigterm_panics is intentionally not marked xfail: it should
fail CI until the panic is fixed in NativeRuntime.shutdown().
"""

import os
import re
import signal
import subprocess
import sys
import time

import pytest

from tests.contrib.uwsgi import run_uwsgi


# uwsgi is not available on Windows
if sys.platform == "win32":
    pytestmark = pytest.mark.skip

uwsgi_app = os.path.join(os.path.dirname(__file__), "uwsgi-app.py")

NUM_WORKERS = 4
# Comfortably above NativeRuntime._DEFAULT_SHUTDOWN_TIMEOUT_MS (3000ms).
DRAIN_TIMEOUT = 10


def _base_cmd(socket_name):
    return [
        "uwsgi",
        "--wsgi-file",
        uwsgi_app,
        "--master",
        "--enable-threads",
        "--lazy-apps",
        "--workers",
        str(NUM_WORKERS),
        "--import",
        "ddtrace.auto",
        "--die-on-term",
        # uwsgi refuses to start unless it has a socket (or stdin is a socket);
        # a unix socket in tmp_path avoids any port-conflict flakiness.
        "--socket",
        socket_name,
    ]


def _terminate(proc):
    """Forcefully stop a uwsgi subprocess that has stalled or hung."""
    if proc.poll() is None:
        proc.terminate()
        try:
            proc.wait(timeout=DRAIN_TIMEOUT)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def _communicate_until(proc, predicate, timeout=DRAIN_TIMEOUT, poll_interval=0.1):
    """Poll a live uwsgi process's combined stdout/stderr via communicate()
    until predicate(output) is true, using a real elapsed-time deadline.
    """
    deadline = time.monotonic() + timeout
    output = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate(proc)
            raise AssertionError("timed out after %s seconds waiting for uwsgi; output so far: %r" % (timeout, output))
        try:
            new_output, _ = proc.communicate(timeout=min(remaining, poll_interval))
            _terminate(proc)
            raise AssertionError("uwsgi exited unexpectedly; output: %r" % new_output)
        except subprocess.TimeoutExpired as exc:
            output = exc.output or b""
        if predicate(output):
            return output


def _wait_for_workers_ready(proc, num_workers):
    def _ready(output):
        worker_pids = [int(pid) for pid in re.findall(rb"^spawned uWSGI worker \d+ .*\(pid: (\d+),", output, re.M)]
        ready = len(re.findall(rb"WSGI app 0 \(mountpoint=''\) ready", output))
        return len(worker_pids) == num_workers and ready == num_workers

    output = _communicate_until(proc, _ready)
    return [int(pid) for pid in re.findall(rb"^spawned uWSGI worker \d+ .*\(pid: (\d+),", output, re.M)]


def _kill_worker_and_collect_until_respawn(proc, worker_pid):
    """Send SIGTERM to one worker and collect all output up to its respawn.

    uwsgi writes the panic traceback (if any), then "DAMN ! worker ... died",
    then "Respawned uWSGI worker" as a strict sequence on the same stream, so
    by the time the respawn line is seen the panic output (if present) has
    already been captured.
    """
    os.kill(worker_pid, signal.SIGTERM)
    return _communicate_until(proc, lambda output: b"Respawned uWSGI worker" in output)


@pytest.fixture
def uwsgi_lazy_app(tmp_path):
    # Do not use pytest tmpdir fixtures which generate directories longer than allowed for a socket file name
    socket_name = str(tmp_path / "uwsgi.sock")

    def _start(*extra_args):
        proc = run_uwsgi(_base_cmd(socket_name))(*extra_args)
        return proc

    started = []

    def _run(*extra_args):
        proc = _start(*extra_args)
        started.append(proc)
        return proc

    yield _run

    for proc in started:
        _terminate(proc)
    if os.path.exists(socket_name):
        os.unlink(socket_name)


def test_uwsgi_worker_sigterm_panics(uwsgi_lazy_app):
    proc = uwsgi_lazy_app()
    worker_pids = _wait_for_workers_ready(proc, NUM_WORKERS)
    assert len(worker_pids) == NUM_WORKERS

    output = _kill_worker_and_collect_until_respawn(proc, worker_pids[0])

    assert b"panicked at" not in output, output
    assert b"PanicException" not in output, output


def test_uwsgi_worker_sigterm_no_panic_with_skip_atexit(uwsgi_lazy_app):
    """--skip-atexit is a pre-existing, effective mitigation: it prevents
    NativeRuntime._atexit from ever being registered, so this must always
    pass regardless of whether the underlying panic is fixed.
    """
    proc = uwsgi_lazy_app("--skip-atexit")
    worker_pids = _wait_for_workers_ready(proc, NUM_WORKERS)
    assert len(worker_pids) == NUM_WORKERS

    output = _kill_worker_and_collect_until_respawn(proc, worker_pids[0])

    assert b"panicked at" not in output, output
    assert b"PanicException" not in output, output
