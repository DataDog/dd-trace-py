"""Unit tests for ddtrace.internal.uwsgi.check_uwsgi() branching logic.

These tests use a fake uwsgi module so they can exercise check_uwsgi()'s
decision logic directly, without spawning a real uwsgi process. End-to-end
coverage against a real uwsgi binary lives in tests/profiling/test_uwsgi.py.
"""

import sys
import types
from typing import Callable
from typing import Optional

import pytest

from ddtrace.internal import uwsgi


class FakeUwsgi(types.ModuleType):
    def __init__(
        self,
        opt: Optional[dict[str, object]] = None,
        numproc: int = 1,
        worker_id: int = 0,
        version_info: tuple[int, int, int] = (2, 0, 30),
    ) -> None:
        super().__init__("uwsgi")
        self.opt = opt or {}
        self.numproc = numproc
        self.version_info = version_info
        self._worker_id = worker_id

    def worker_id(self) -> int:
        return self._worker_id


@pytest.fixture
def fake_uwsgi(monkeypatch: pytest.MonkeyPatch) -> Callable[..., FakeUwsgi]:
    def _install(
        opt: Optional[dict[str, object]] = None,
        numproc: int = 1,
        worker_id: int = 0,
        version_info: tuple[int, int, int] = (2, 0, 30),
    ) -> FakeUwsgi:
        fake = FakeUwsgi(opt=opt, numproc=numproc, worker_id=worker_id, version_info=version_info)
        monkeypatch.setitem(sys.modules, "uwsgi", fake)
        return fake

    return _install


def test_no_uwsgi_module_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "uwsgi", None)
    # importing "uwsgi" while sys.modules["uwsgi"] is None raises ImportError
    # Check the runtime return contract even though check_uwsgi is annotated to return None.
    check = uwsgi.check_uwsgi()  # type: ignore[func-returns-value]
    assert check is None


def test_missing_opt_raises_config_error(fake_uwsgi: Callable[..., FakeUwsgi], monkeypatch: pytest.MonkeyPatch) -> None:
    fake = fake_uwsgi()
    monkeypatch.delattr(fake, "opt")
    with pytest.raises(uwsgi.uWSGIConfigError):
        uwsgi.check_uwsgi()


def test_threads_not_enabled_raises_config_error(fake_uwsgi: Callable[..., FakeUwsgi]) -> None:
    fake_uwsgi(opt={})
    with pytest.raises(uwsgi.uWSGIConfigError, match="enable-threads"):
        uwsgi.check_uwsgi()


@pytest.mark.parametrize("threads_opt", [{"enable-threads": True}, {"threads": "2"}])
def test_threads_enabled_single_process_is_ordinary(
    fake_uwsgi: Callable[..., FakeUwsgi], threads_opt: dict[str, object]
) -> None:
    fake_uwsgi(opt=threads_opt, numproc=1, worker_id=0)
    # Single process: ordinary process handling, no exception.
    assert uwsgi.check_uwsgi() is None  # type: ignore[func-returns-value]


def test_non_lazy_multi_process_without_master_raises(fake_uwsgi: Callable[..., FakeUwsgi]) -> None:
    fake_uwsgi(opt={"enable-threads": True}, numproc=2, worker_id=0)
    with pytest.raises(uwsgi.uWSGIConfigError, match="master option must be enabled"):
        uwsgi.check_uwsgi()


def test_non_lazy_multi_process_with_master_is_deferred_to_worker(fake_uwsgi: Callable[..., FakeUwsgi]) -> None:
    """Without lazy-apps or py-call-uwsgi-fork-hooks, the master process defers to postfork."""
    fake_uwsgi(opt={"enable-threads": True, "master": True}, numproc=2, worker_id=0)
    with pytest.raises(uwsgi.uWSGIMasterProcess):
        uwsgi.check_uwsgi()


def test_lazy_apps_multi_process_is_ordinary(fake_uwsgi: Callable[..., FakeUwsgi]) -> None:
    fake_uwsgi(opt={"enable-threads": True, "lazy-apps": True, "master": True}, numproc=2, worker_id=0)
    # lazy-apps: each worker loads independently, no special-casing needed.
    assert uwsgi.check_uwsgi() is None  # type: ignore[func-returns-value]


def test_fork_hooks_multi_process_with_master_can_defer_to_worker(fake_uwsgi: Callable[..., FakeUwsgi]) -> None:
    """The profiler can defer startup without changing the general fork-hook lifecycle."""
    fake_uwsgi(
        opt={"enable-threads": True, "master": True, "py-call-uwsgi-fork-hooks": True},
        numproc=2,
        worker_id=0,
    )
    with pytest.raises(uwsgi.uWSGIMasterProcess):
        uwsgi.check_uwsgi(defer_in_master=True)


@pytest.mark.parametrize("defer_in_master", [False, True])
def test_fork_hooks_multi_process_without_master_is_ordinary(
    fake_uwsgi: Callable[..., FakeUwsgi], defer_in_master: bool
) -> None:
    """py-call-uwsgi-fork-hooks does not require --master: uwsgi's worker spawn path
    (and therefore its fork-hook invocation) is the same with or without a master.
    """
    fake_uwsgi(
        opt={"enable-threads": True, "py-call-uwsgi-fork-hooks": True},
        numproc=2,
        worker_id=0,
    )
    assert uwsgi.check_uwsgi(defer_in_master=defer_in_master) is None  # type: ignore[func-returns-value]


@pytest.mark.parametrize("defer_in_master", [False, True])
def test_fork_hooks_with_master_only_registers_postfork_when_requested(
    fake_uwsgi: Callable[..., FakeUwsgi], monkeypatch: pytest.MonkeyPatch, defer_in_master: bool
) -> None:
    fake_uwsgi(
        opt={"enable-threads": True, "master": True, "py-call-uwsgi-fork-hooks": True},
        numproc=2,
        worker_id=0,
    )
    callbacks: list[Callable[[], None]] = []
    decorators = types.ModuleType("uwsgidecorators")
    setattr(decorators, "postfork", callbacks.append)
    monkeypatch.setitem(sys.modules, "uwsgidecorators", decorators)

    def callback() -> None:
        pass

    if defer_in_master:
        with pytest.raises(uwsgi.uWSGIMasterProcess):
            uwsgi.check_uwsgi(worker_callback=callback, defer_in_master=True)
        assert callbacks == [callback]
    else:
        assert uwsgi.check_uwsgi(worker_callback=callback) is None  # type: ignore[func-returns-value]
        assert callbacks == []


def test_fork_hooks_ignored_on_worker(fake_uwsgi: Callable[..., FakeUwsgi]) -> None:
    """worker_id() != 0 identifies a worker process; the master-only branch never applies."""
    fake_uwsgi(opt={"enable-threads": True, "master": True}, numproc=2, worker_id=1)
    assert uwsgi.check_uwsgi() is None  # type: ignore[func-returns-value]


def test_old_uwsgi_lazy_without_skip_atexit_warns(fake_uwsgi: Callable[..., FakeUwsgi]) -> None:
    fake_uwsgi(
        opt={"enable-threads": True, "lazy-apps": True},
        numproc=1,
        worker_id=0,
        version_info=(2, 0, 29),
    )
    with pytest.raises(uwsgi.uWSGIConfigDeprecationWarning):
        uwsgi.check_uwsgi()


@pytest.mark.parametrize("defer_in_master", [False, True])
def test_old_uwsgi_fork_hooks_without_skip_atexit_is_unaffected(
    fake_uwsgi: Callable[..., FakeUwsgi], defer_in_master: bool
) -> None:
    """The skip-atexit warning remains specific to lazy-apps/lazy."""
    fake_uwsgi(
        opt={"enable-threads": True, "master": True, "py-call-uwsgi-fork-hooks": True},
        numproc=2,
        worker_id=0,
        version_info=(2, 0, 29),
    )
    if defer_in_master:
        with pytest.raises(uwsgi.uWSGIMasterProcess):
            uwsgi.check_uwsgi(defer_in_master=True)
    else:
        assert uwsgi.check_uwsgi() is None  # type: ignore[func-returns-value]
