"""Tests for process_type tag in profiling fork scenarios.

Verifies that the profiler's ``get_process_role()`` helper returns the correct
value in Gunicorn-style pre-fork, uWSGI postfork, and multiprocessing spawn
scenarios, so that ``ddup.upload()`` can emit the right ``process_type`` tag
(``'main'`` or ``'worker'``) to the Datadog intake.
"""

from __future__ import annotations

from typing import Generator

import pytest

from ddtrace.profiling import profiler


@pytest.fixture(autouse=True)
def _reset_profiler_active_instance() -> Generator[None, None, None]:
    yield
    profiler.Profiler._active_instance = None


# ---------------------------------------------------------------------------
# Gunicorn-style pre-fork: main forks workers
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_gunicorn_style_fork_parent_is_main() -> None:
    """After forking Gunicorn workers the main process reports role 'main'."""
    import os

    from ddtrace.internal.runtime import get_process_role

    assert get_process_role() is None

    child = os.fork()
    if child == 0:
        os._exit(0)

    os.waitpid(child, 0)
    assert get_process_role() == "main", get_process_role()


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_gunicorn_style_fork_child_is_worker() -> None:
    """Gunicorn worker process (forked child) reports role 'worker'."""
    import os

    from ddtrace.internal.runtime import get_process_role

    assert get_process_role() is None

    child = os.fork()
    if child == 0:
        assert get_process_role() == "worker", get_process_role()
        os._exit(0)

    _, status = os.waitpid(child, 0)
    assert os.WEXITSTATUS(status) == 0


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_gunicorn_style_multiple_workers_all_report_worker() -> None:
    """Each of N Gunicorn workers reports 'worker'; main reports 'main' after all forks."""
    import os

    from ddtrace.internal.runtime import get_process_role

    assert get_process_role() is None

    pids = []
    for _ in range(2):
        pid = os.fork()
        if pid == 0:
            assert get_process_role() == "worker", get_process_role()
            os._exit(0)
        pids.append(pid)

    for pid in pids:
        _, status = os.waitpid(pid, 0)
        assert os.WEXITSTATUS(status) == 0

    assert get_process_role() == "main", get_process_role()


# ---------------------------------------------------------------------------
# uWSGI postfork simulation: profiler started in worker via _start_on_fork()
# ---------------------------------------------------------------------------


def test_uwsgi_postfork_worker_role_via_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """uWSGI postfork worker: get_process_role() returns 'worker' when parent runtime ID is set.

    Uses the same monkeypatching pattern as test_uwsgi.py: simulate the main
    process by raising uWSGIMasterProcess, then call _start_on_fork() as the
    postfork callback would.  The role check verifies the detection primitive
    under mock.
    """
    import ddtrace.internal.runtime as _runtime_mod

    def _raise_main(*args: object, **kwargs: object) -> None:
        raise profiler.uwsgi.uWSGIMasterProcess()

    monkeypatch.setattr(profiler.uwsgi, "check_uwsgi", _raise_main)

    # Simulate what happens to _PARENT_RUNTIME_ID after a real fork in a worker.
    monkeypatch.setattr(_runtime_mod, "_PARENT_RUNTIME_ID", "fake-parent-id")

    from ddtrace.internal.runtime import get_process_role

    assert get_process_role() == "worker"

    p = profiler.Profiler()
    p.start()
    p._start_on_fork()
    assert profiler.Profiler._active_instance is p
    p.stop(flush=False)


def test_uwsgi_main_role_via_mock(monkeypatch: pytest.MonkeyPatch) -> None:
    """uWSGI main process: get_process_role() returns None before any fork."""
    import ddtrace.internal.forksafe as _forksafe
    import ddtrace.internal.runtime as _runtime_mod

    # Reset process-lifetime state that may be True if subprocess.run() was called
    # earlier in this pytest session (subprocess.run uses os.fork on Linux).
    monkeypatch.setattr(_forksafe, "_forked", False)
    monkeypatch.setattr(_runtime_mod, "_PARENT_RUNTIME_ID", None)

    from ddtrace.internal.runtime import get_process_role

    # Main process has no parent runtime ID and has not yet forked (no workers yet).
    assert get_process_role() is None


# ---------------------------------------------------------------------------
# multiprocessing spawn / forkserver (env-var seeded _DD_PARENT_PY_SESSION_ID)
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(
    env={
        "_DD_PARENT_PY_SESSION_ID": "parent-session-id-spawn",
        "DD_TRACE_SUBPROCESS_ENABLED": "false",
    }
)
def test_spawn_child_is_worker() -> None:
    """Multiprocessing spawn/forkserver child (env-var parent session id) reports 'worker'."""
    from ddtrace.internal.runtime import get_process_role

    assert get_process_role() == "worker", get_process_role()


# ---------------------------------------------------------------------------
# Single process: no role (no tag emitted)
# ---------------------------------------------------------------------------


@pytest.mark.subprocess
def test_single_process_no_role() -> None:
    """Standalone single-process application: get_process_role() returns None."""
    from ddtrace.internal.runtime import get_process_role

    assert get_process_role() is None


# ---------------------------------------------------------------------------
# ddup module wiring: get_process_role is imported into the _ddup namespace
# so that upload() can call it.
# ---------------------------------------------------------------------------


def test_get_process_role_available_in_ddup_namespace() -> None:
    """get_process_role must be importable from the _ddup extension module.

    upload() looks it up by name from its module globals at call time.  This
    test confirms the symbol is present in the Cython module's namespace so that
    a subsequent monkeypatch in integration tests can override it.
    """
    try:
        import ddtrace.internal.datadog.profiling.ddup._ddup as _ddup_mod
    except ImportError:
        pytest.skip("ddup native extension not available")

    assert hasattr(_ddup_mod, "get_process_role"), (
        "get_process_role not found in _ddup module namespace; "
        "upload() would call the wrong function after a monkeypatch"
    )
    from ddtrace.internal.runtime import get_process_role

    assert _ddup_mod.get_process_role is get_process_role
