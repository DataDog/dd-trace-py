import os
import sys

import pytest
import six
from six.moves import _thread

from ddtrace.internal import forksafe


def test_forksafe():
    state = []

    @forksafe.register
    def after_in_child():
        state.append(1)

    def my_func():
        return state

    pid = os.fork()

    if pid == 0:
        # child
        assert my_func() == [1]
        os._exit(12)
    else:
        assert my_func() == []

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_registry():
    state = []

    @forksafe.register
    def after_in_child_1():
        state.append(1)

    @forksafe.register
    def after_in_child_2():
        state.append(2)

    @forksafe.register
    def after_in_child_3():
        state.append(3)

    forksafe.ddtrace_after_in_child()
    assert state == [1, 2, 3]


def test_duplicates():
    state = []

    @forksafe.register
    def hook():
        state.append(1)

    def f1():
        return state

    def f2():
        return state

    def f3():
        return state

    pid = os.fork()

    if pid == 0:
        # child
        assert f1() == f2() == f3() == [1]
        os._exit(12)
    else:
        assert f1() == f2() == f3() == []

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_method_usage():
    class A:
        def __init__(self):
            self.state = 0
            forksafe.register(self.after_fork)

        def after_fork(self):
            self.state = 1

        def method(self):
            return self.state

    a = A()
    pid = os.fork()
    if pid == 0:
        # child
        assert a.method() == 1
        os._exit(12)
    else:
        assert a.method() == 0

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_hook_exception():
    state = []

    @forksafe.register
    def after_in_child():
        raise ValueError

    @forksafe.register
    def state_append_1():
        forksafe.register(state.append(1))

    @forksafe.register
    def state_append_3():
        forksafe.register(state.append(3))

    def f1():
        return state

    def f2():
        return state

    def f3():
        return state

    pid = os.fork()
    if pid == 0:
        # child
        assert f1() == f2() == f3() == [1, 3]
        os._exit(12)
    else:
        assert f1() == f2() == f3() == []

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


if six.PY2:
    lock_release_exc_type = _thread.error
else:
    lock_release_exc_type = RuntimeError


def test_lock_basic():
    # type: (...) -> None
    """Check that a forksafe.Lock implements the correct threading.Lock interface"""
    lock = forksafe.Lock()
    assert lock.acquire()
    assert lock.release() is None
    with pytest.raises(lock_release_exc_type):
        lock.release()


def test_lock_fork():
    """Check that a forksafe.Lock is reset after a fork().

    This test fails with a regular threading.Lock.
    """
    lock = forksafe.Lock()
    lock.acquire()

    pid = os.fork()

    if pid == 0:
        # child
        assert lock.acquire()
        lock.release()
        with pytest.raises(lock_release_exc_type):
            lock.release()
        os._exit(12)

    lock.release()

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_rlock_basic():
    # type: (...) -> None
    """Check that a forksafe.RLock implements the correct threading.RLock interface"""
    lock = forksafe.RLock()
    assert lock.acquire()
    assert lock.acquire()
    assert lock.release() is None
    assert lock.release() is None
    with pytest.raises(RuntimeError):
        lock.release()


def test_rlock_fork():
    """Check that a forksafe.RLock is reset after a fork().

    This test fails with a regular threading.RLock.
    """
    lock = forksafe.RLock()
    lock.acquire()
    lock.acquire()

    pid = os.fork()

    if pid == 0:
        # child
        assert lock.acquire()
        lock.release()
        with pytest.raises(RuntimeError):
            lock.release()
        os._exit(12)

    lock.release()
    lock.release()

    with pytest.raises(RuntimeError):
        lock.release()

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_event_basic():
    # type: (...) -> None
    """Check that a forksafe.Event implements the correct threading.Event interface"""
    event = forksafe.Event()
    assert event.is_set() is False
    event.set()
    assert event.wait()
    assert event.is_set() is True
    event.clear()


def test_event_fork():
    """Check that a forksafe.Event is reset after a fork().

    This test fails with a regular threading.Event.
    """
    event = forksafe.Event()
    event.set()

    pid = os.fork()

    if pid == 0:
        # child
        assert event.is_set() is False
        os._exit(12)

    assert event.is_set() is True

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_double_fork():
    state = []

    @forksafe.register
    def fn():
        state.append(1)

    child = os.fork()

    if child == 0:
        assert state == [1]
        child2 = os.fork()

        if child2 == 0:
            assert state == [1, 1]
            os._exit(42)

        pid, status = os.waitpid(child2, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 42
        os._exit(42)

    assert state == []
    pid, status = os.waitpid(child, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42


@pytest.mark.subprocess(
    out="" if (3,) < sys.version_info < (3, 7) else ("CTCTCT" if sys.platform == "darwin" else "CCCTTT"),
    err=None,
    env=dict(_DD_TRACE_GEVENT_HUB_PATCHED="true"),
)
def test_gevent_reinit_patch():
    import os
    import sys

    from ddtrace.internal import forksafe
    from ddtrace.internal.periodic import PeriodicService

    class TestService(PeriodicService):
        def __init__(self):
            super(TestService, self).__init__(interval=1.0)

        def periodic(self):
            sys.stdout.write("T")

    service = TestService()
    service.start()

    def restart_service():
        global service

        service.stop()
        service = TestService()
        service.start()

    forksafe.register(restart_service)

    import gevent  # noqa

    def run_child():
        global service

        # We mimic what gunicorn does in child processes
        gevent.monkey.patch_all()
        gevent.hub.reinit()

        sys.stdout.write("C")

        gevent.sleep(1.5)

        service.stop()

    def fork_workers(num):
        for _ in range(num):
            if os.fork() == 0:
                run_child()
                sys.exit(0)

    fork_workers(3)

    service.stop()
