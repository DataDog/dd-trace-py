from collections import Counter
import os
import sys

import pytest

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
    """This verifies that registered hooks are called after a fork.

    forking is done because some of the automatically-registered hooks called in forksafe.ddtrace_after_in_child have
    a destructive effect on the parent process' trace data.

    The original state is checked to be unmodified in the parent after the fork.
    """
    state = ["before_fork"]

    @forksafe.register
    def after_in_child_1():
        state.append("after_in_child_1")

    @forksafe.register
    def after_in_child_2():
        state.append("after_in_child_2")

    @forksafe.register
    def after_in_child_3():
        state.append("after_in_child_3")

    pid = os.fork()

    if pid == 0:
        assert state == ["before_fork", "after_in_child_1", "after_in_child_2", "after_in_child_3"]
        os._exit(12)
    else:
        assert state == ["before_fork"]

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


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


@pytest.mark.skipif(sys.version_info > (3, 12), reason="fails on 3.13")
@pytest.mark.subprocess(
    out=lambda _: Counter(_) == {"C": 3, "T": 4},
    err=None,
    ddtrace_run=True,
)
def test_gevent_gunicorn_behaviour():
    # emulate how sitecustomize.py cleans up imported modules
    # to avoid problems with threads/forks that we saw previously
    # when running gunicorn with gevent workers

    import os
    import sys

    assert "gevent" not in sys.modules

    assert "ddtrace.internal" in sys.modules
    assert "ddtrace.internal.periodic" in sys.modules

    import atexit

    from ddtrace.internal import forksafe
    from ddtrace.internal.periodic import PeriodicService

    class TestService(PeriodicService):
        def __init__(self):
            super(TestService, self).__init__(interval=0.1)
            self._has_run = False

        def periodic(self):
            if not self._has_run:
                sys.stdout.write("T")
                sys.stdout.flush()
                self._has_run = True

    service = TestService()
    service.start()
    atexit.register(lambda: service.stop() and service.join(1))

    def restart_service():
        global service
        service.stop()
        service = TestService()
        service.start()

    forksafe.register(restart_service)

    # ---- Application code ----

    import sys  # noqa:F401

    import gevent.hub  # noqa:F401
    import gevent.monkey  # noqa:F401

    def run_child():
        # We mimic what gunicorn does in child processes
        gevent.monkey.patch_all()
        gevent.hub.reinit()

        sys.stdout.write("C")

        gevent.sleep(1)

    def fork_workers(num):
        for _ in range(num):
            if os.fork() == 0:
                run_child()
                sys.exit(0)

    fork_workers(3)

    gevent.sleep(1)

    exit()
