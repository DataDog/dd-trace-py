"""Unit tests for ddtrace.internal.uwsgi.check_uwsgi() branching logic.

These tests use a fake ``uwsgi`` module so they can exercise check_uwsgi()'s
decision logic directly, without spawning a real uwsgi process. End-to-end
coverage against a real uwsgi binary lives in tests/profiling/test_uwsgi.py.
"""

import sys
import types

import pytest

from ddtrace.internal import uwsgi


class FakeUwsgi(types.ModuleType):
    def __init__(self, opt=None, numproc=1, worker_id=0, version_info=(2, 0, 30)):
        super().__init__("uwsgi")
        self.opt = opt or {}
        self.numproc = numproc
        self.version_info = version_info
        self._worker_id = worker_id

    def worker_id(self):
        return self._worker_id


@pytest.fixture
def fake_uwsgi(monkeypatch):
    def _install(**kwargs):
        fake = FakeUwsgi(**kwargs)
        monkeypatch.setitem(sys.modules, "uwsgi", fake)
        return fake

    return _install


def test_no_uwsgi_module_is_a_noop(monkeypatch):
    monkeypatch.setitem(sys.modules, "uwsgi", None)
    # importing "uwsgi" while sys.modules["uwsgi"] is None raises ImportError
    check = uwsgi.check_uwsgi()
    assert check is None


def test_missing_opt_raises_config_error(fake_uwsgi, monkeypatch):
    fake = fake_uwsgi()
    monkeypatch.delattr(fake, "opt")
    with pytest.raises(uwsgi.uWSGIConfigError):
        uwsgi.check_uwsgi()


def test_threads_not_enabled_raises_config_error(fake_uwsgi):
    fake_uwsgi(opt={})
    with pytest.raises(uwsgi.uWSGIConfigError, match="enable-threads"):
        uwsgi.check_uwsgi()


@pytest.mark.parametrize("threads_opt", [{"enable-threads": True}, {"threads": "2"}])
def test_threads_enabled_single_process_is_ordinary(fake_uwsgi, threads_opt):
    fake_uwsgi(opt=threads_opt, numproc=1, worker_id=0)
    # Single process: ordinary process handling, no exception.
    assert uwsgi.check_uwsgi() is None


def test_non_lazy_multi_process_without_master_raises(fake_uwsgi):
    fake_uwsgi(opt={"enable-threads": True}, numproc=2, worker_id=0)
    with pytest.raises(uwsgi.uWSGIConfigError, match="master option must be enabled"):
        uwsgi.check_uwsgi()


def test_non_lazy_multi_process_with_master_is_deferred_to_worker(fake_uwsgi):
    """Without lazy-apps or py-call-uwsgi-fork-hooks, the master process defers to postfork."""
    fake_uwsgi(opt={"enable-threads": True, "master": True}, numproc=2, worker_id=0)
    with pytest.raises(uwsgi.uWSGIMasterProcess):
        uwsgi.check_uwsgi()


def test_lazy_apps_multi_process_is_ordinary(fake_uwsgi):
    fake_uwsgi(opt={"enable-threads": True, "lazy-apps": True, "master": True}, numproc=2, worker_id=0)
    # lazy-apps: each worker loads independently, no special-casing needed.
    assert uwsgi.check_uwsgi() is None


def test_fork_hooks_multi_process_with_master_is_ordinary(fake_uwsgi):
    """py-call-uwsgi-fork-hooks makes real os.register_at_fork hooks fire on uwsgi's
    fork, so ddtrace can be treated like any other regular forking process.
    """
    fake_uwsgi(
        opt={"enable-threads": True, "master": True, "py-call-uwsgi-fork-hooks": True},
        numproc=2,
        worker_id=0,
    )
    assert uwsgi.check_uwsgi() is None


def test_fork_hooks_multi_process_without_master_is_ordinary(fake_uwsgi):
    """py-call-uwsgi-fork-hooks does not require --master: uwsgi's worker spawn path
    (and therefore its fork-hook invocation) is the same with or without a master.
    """
    fake_uwsgi(
        opt={"enable-threads": True, "py-call-uwsgi-fork-hooks": True},
        numproc=2,
        worker_id=0,
    )
    assert uwsgi.check_uwsgi() is None


def test_fork_hooks_does_not_register_postfork_callback(fake_uwsgi, monkeypatch):
    """When fork hooks are active, check_uwsgi should not need uwsgidecorators at all."""
    fake_uwsgi(
        opt={"enable-threads": True, "master": True, "py-call-uwsgi-fork-hooks": True},
        numproc=2,
        worker_id=0,
    )
    monkeypatch.setitem(sys.modules, "uwsgidecorators", None)

    called = []
    assert uwsgi.check_uwsgi(worker_callback=lambda: called.append(True)) is None
    assert called == []


def test_fork_hooks_ignored_on_worker(fake_uwsgi):
    """worker_id() != 0 identifies a worker process; the master-only branch never applies."""
    fake_uwsgi(opt={"enable-threads": True, "master": True}, numproc=2, worker_id=1)
    assert uwsgi.check_uwsgi() is None


def test_old_uwsgi_lazy_without_skip_atexit_warns(fake_uwsgi):
    fake_uwsgi(
        opt={"enable-threads": True, "lazy-apps": True},
        numproc=1,
        worker_id=0,
        version_info=(2, 0, 29),
    )
    with pytest.raises(uwsgi.uWSGIConfigDeprecationWarning):
        uwsgi.check_uwsgi()


def test_old_uwsgi_fork_hooks_without_skip_atexit_is_unaffected(fake_uwsgi):
    """The skip-atexit deprecation warning is specific to lazy-apps/lazy; it does not
    apply to the py-call-uwsgi-fork-hooks alternative, which does not reload the app.
    """
    fake_uwsgi(
        opt={"enable-threads": True, "master": True, "py-call-uwsgi-fork-hooks": True},
        numproc=2,
        worker_id=0,
        version_info=(2, 0, 29),
    )
    assert uwsgi.check_uwsgi() is None
