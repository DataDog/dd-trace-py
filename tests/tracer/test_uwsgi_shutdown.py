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


def _readline(stdout, deadline_lines=2000):
    """Blocking readline with a hard cap on total lines as a hang backstop.

    Mirrors the synchronization style already used in
    tests/profiling/test_uwsgi.py (block on readline() rather than sleeping),
    which is the established, non-flaky pattern for uwsgi tests in this repo.
    """
    for _ in range(deadline_lines):
        line = stdout.readline()
        if line == b"":
            return None
        yield line
    raise AssertionError("uwsgi produced more than %d lines without reaching the expected state" % deadline_lines)


def _wait_for_workers_ready(stdout, num_workers):
    worker_pids = []
    ready = 0
    for line in _readline(stdout):
        decoded = line.decode(errors="replace")
        if "WSGI app 0 (mountpoint='') ready" in decoded:
            ready += 1
        else:
            m = re.match(r"^spawned uWSGI worker \d+ .*\(pid: (\d+),", decoded)
            if m:
                worker_pids.append(int(m.group(1)))
        if len(worker_pids) == num_workers and ready == num_workers:
            break
    return worker_pids


def _kill_worker_and_collect_until_respawn(stdout, worker_pid):
    """Send SIGTERM to one worker and collect all output up to its respawn.

    uwsgi writes the panic traceback (if any), then "DAMN ! worker ... died",
    then "Respawned uWSGI worker" as a strict sequence on the same stream, so
    by the time the respawn line is seen the panic output (if present) has
    already been captured.
    """
    os.kill(worker_pid, signal.SIGTERM)
    output = b""
    for line in _readline(stdout):
        output += line
        if b"Respawned uWSGI worker" in line:
            break
    return output


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
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=DRAIN_TIMEOUT)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
    if os.path.exists(socket_name):
        os.unlink(socket_name)


def test_uwsgi_worker_sigterm_panics(uwsgi_lazy_app):
    proc = uwsgi_lazy_app()
    worker_pids = _wait_for_workers_ready(proc.stdout, NUM_WORKERS)
    assert len(worker_pids) == NUM_WORKERS

    output = _kill_worker_and_collect_until_respawn(proc.stdout, worker_pids[0])

    assert b"panicked at" not in output, output
    assert b"PanicException" not in output, output


def test_uwsgi_worker_sigterm_no_panic_with_skip_atexit(uwsgi_lazy_app):
    """--skip-atexit is a pre-existing, effective mitigation: it prevents
    NativeRuntime._atexit from ever being registered, so this must always
    pass regardless of whether the underlying panic is fixed.
    """
    proc = uwsgi_lazy_app("--skip-atexit")
    worker_pids = _wait_for_workers_ready(proc.stdout, NUM_WORKERS)
    assert len(worker_pids) == NUM_WORKERS

    output = _kill_worker_and_collect_until_respawn(proc.stdout, worker_pids[0])

    assert b"panicked at" not in output, output
    assert b"PanicException" not in output, output
