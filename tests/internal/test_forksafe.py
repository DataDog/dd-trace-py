from collections import Counter
import functools
import pickle
import threading
from types import ModuleType

import cloudpickle
import pytest

from ddtrace.internal import forksafe


@pytest.mark.subprocess
def test_forksafe():
    import os

    from ddtrace.internal import forksafe

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


@pytest.mark.subprocess
def test_registry():
    """This verifies that registered hooks are called after a fork.

    forking is done because some of the automatically-registered hooks called in forksafe.ddtrace_after_in_child have
    a destructive effect on the parent process' trace data.

    The original state is checked to be unmodified in the parent after the fork.
    """
    import os

    from ddtrace.internal import forksafe

    state = ["initial"]

    @forksafe.register
    def after_in_child_1():
        state.append("after_in_child_1")

    @forksafe.register
    def after_in_child_2():
        state.append("after_in_child_2")

    @forksafe.register
    def after_in_child_3():
        state.append("after_in_child_3")

    @forksafe.register_after_parent
    def after_in_parent():
        state.append("after_in_parent")

    @forksafe.register_before_fork
    def before_fork():
        state.append("before_fork")

    pid = os.fork()

    if pid == 0:
        assert state == ["initial", "before_fork", "after_in_child_1", "after_in_child_2", "after_in_child_3"]
        os._exit(12)
    else:
        assert state == ["initial", "before_fork", "after_in_parent"]

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


@pytest.mark.subprocess
def test_method_usage():
    import os

    from ddtrace.internal import forksafe

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


@pytest.mark.subprocess(err=None)
def test_hook_exception():
    import os

    from ddtrace.internal import forksafe

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


@pytest.mark.subprocess(err=None)
def test_hook_panic_exception():
    """A native hook can raise pyo3_runtime.PanicException on a Rust panic.

    PanicException subclasses BaseException directly rather than Exception, by
    pyo3's design. Left uncaught, it would abort run_hooks' loop and silently
    skip every hook registered after it, so it must be swallowed the same way
    a plain Exception is; anything else must still propagate.
    """
    import os

    from ddtrace.internal import forksafe

    state = []

    class PanicException(BaseException):
        pass

    PanicException.__module__ = "pyo3_runtime"

    @forksafe.register
    def panicking_hook():
        raise PanicException("failed to wake I/O driver")

    @forksafe.register
    def after_panicking_hook():
        state.append(1)

    pid = os.fork()
    if pid == 0:
        # child
        assert state == [1]
        os._exit(12)
    else:
        assert state == []

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


@pytest.mark.subprocess(err=None)
def test_hook_base_exception_not_a_panic_still_propagates():
    """A BaseException that is not a pyo3 panic must still propagate and abort
    the hook loop, unlike the panic-specific case above.
    """
    import os

    from ddtrace.internal import forksafe

    state = []

    @forksafe.register
    def raising_hook():
        raise SystemExit(7)

    @forksafe.register
    def after_raising_hook():
        state.append(1)

    pid = os.fork()
    if pid == 0:
        # child: SystemExit isn't a panic, so run_hooks re-raises it, aborting
        # the loop -- the hook registered after the raising one never runs.
        # CPython reports the propagated exception as "Exception ignored in"
        # (PyErr_WriteUnraisable) rather than treating it as fatal.
        assert state == []
        os._exit(12)
    else:
        assert state == []

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12


def test_event_basic() -> None:
    """Check that a forksafe.Event implements the correct threading.Event interface"""
    event = forksafe.Event()
    assert event.is_set() is False
    event.set()
    assert event.wait()
    assert event.is_set() is True
    event.clear()


@pytest.mark.subprocess
def test_event_fork():
    """Check that a forksafe.Event is reset after a fork().

    This test fails with a regular threading.Event.
    """
    import os

    from ddtrace.internal import forksafe

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


@pytest.mark.subprocess
def test_double_fork():
    import os

    from ddtrace.internal import forksafe

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


@pytest.mark.subprocess(timeout=30)
def test_fork_hooks_are_serialized():
    import threading

    from ddtrace.internal import forksafe

    forksafe.ddtrace_before_fork()

    second_entered = threading.Event()

    def second_fork():
        forksafe.ddtrace_before_fork()
        second_entered.set()
        forksafe.ddtrace_after_in_parent()

    t = threading.Thread(target=second_fork)
    t.start()

    # A concurrent fork must wait for the current one to complete its hooks.
    assert not second_entered.wait(timeout=0.2)

    forksafe.ddtrace_after_in_parent()

    assert second_entered.wait(timeout=5)
    t.join()


@pytest.mark.subprocess(timeout=30)
def test_unpaired_after_fork_hooks():
    import threading

    from ddtrace.internal import forksafe

    # Without a matching before-fork call from this thread, the after-parent
    # hooks must not release a lock they do not own.
    forksafe.ddtrace_after_in_parent()

    # Simulate a C-level fork (e.g. uWSGI master forking a worker) while
    # another thread was in the middle of a fork: the child inherits the lock
    # held by a thread that does not exist, and runs only the child hooks.
    held = threading.Event()

    def mid_fork():
        forksafe.ddtrace_before_fork()
        held.set()

    t = threading.Thread(target=mid_fork)
    t.start()
    assert held.wait(timeout=5)
    t.join()

    forksafe.ddtrace_after_in_child()

    done = threading.Event()

    def next_fork():
        forksafe.ddtrace_before_fork()
        forksafe.ddtrace_after_in_parent()
        done.set()

    threading.Thread(target=next_fork, daemon=True).start()
    assert done.wait(timeout=5), "fork lock still held after the child hooks"


@pytest.mark.subprocess(timeout=120)
def test_concurrent_forks_from_threads():
    import os
    import sys
    import threading
    import time
    import warnings

    from ddtrace.internal import threads

    # Use plain threads: concurrent.futures.thread registers at-fork hooks
    # around a lock of its own, which serializes forks and hides the issue
    # whenever that module is imported after ddtrace.
    assert "concurrent.futures.thread" not in sys.modules

    # Python warns about forking a multi-threaded process; that is the point.
    # catch_warnings() is not thread-safe, so set the filter process-wide.
    warnings.simplefilter("ignore", DeprecationWarning)

    # Give the fork hooks periodic threads to stop and restart around each fork.
    periodic = [threads.PeriodicThread(0.01, lambda: None, name=f"test-periodic-{i}") for i in range(4)]
    for t in periodic:
        t.start()

    statuses = []

    def fork_and_wait():
        for _ in range(6):
            pid = os.fork()
            if pid == 0:
                os._exit(42)

            deadline = time.monotonic() + 10
            while True:
                wpid, status = os.waitpid(pid, os.WNOHANG)
                if wpid:
                    statuses.append(os.WEXITSTATUS(status))
                    break
                if time.monotonic() > deadline:
                    os.kill(pid, 9)
                    os.waitpid(pid, 0)
                    statuses.append(None)
                    break
                time.sleep(0.01)

    forkers = [threading.Thread(target=fork_and_wait) for _ in range(16)]
    for f in forkers:
        f.start()
    for f in forkers:
        f.join()

    assert statuses == [42] * len(statuses), f"{statuses.count(None)} children hung"

    for t in periodic:
        t.stop()
        t.join()


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

    from ddtrace.internal.periodic import PeriodicService

    class TestService(PeriodicService):
        def __init__(self):
            super().__init__(interval=0.1)
            self._has_run = False
            self._pid = os.getpid()

        def reset(self):
            self._has_run = False

        def periodic(self):
            if not self._has_run or self._pid != os.getpid():
                sys.stdout.write("T")
                sys.stdout.flush()
                self._has_run = True
                self._pid = os.getpid()

    service = TestService()
    service.start()
    atexit.register(lambda: service.stop() and service.join(1))

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


def test_unregister_unregistered_partial_does_not_crash():
    class CallableInstance:
        def __call__(self):
            pass

    def _hook():
        pass

    partial_hook = functools.partial(_hook)
    instance_hook = CallableInstance()

    # Neither was ever registered, so each unregister hits the ValueError
    # branch and the warning log call must succeed.
    forksafe.unregister(partial_hook)
    forksafe.unregister(instance_hook)
    forksafe.unregister_parent(partial_hook)
    forksafe.unregister_parent(instance_hook)
    forksafe.unregister_before_fork(partial_hook)
    forksafe.unregister_before_fork(instance_hook)


@pytest.mark.parametrize("serializer", [pickle, cloudpickle], ids=["pickle", "cloudpickle"])
def test_lock_pickle_roundtrip(serializer: ModuleType) -> None:
    """A forksafe.Lock must survive (cloud)pickle as a fresh, unlocked lock.

    Ray Serve cloudpickles deployment objects that transitively reference the
    global ddtrace config, which holds a forksafe.Lock. Without a __reduce__,
    this fails with "cannot pickle '_thread.lock'" (AIPTS-1715).
    """
    lock: threading.Lock = forksafe.Lock()
    lock.acquire()  # locked in the source process

    data: bytes = serializer.dumps(lock)
    restored: threading.Lock = serializer.loads(data)

    assert isinstance(restored, forksafe.ResetObject)
    assert restored.acquire(blocking=False) is True
    restored.release()
    assert restored in forksafe._resetable_objects


@pytest.mark.parametrize("serializer", [pickle, cloudpickle], ids=["pickle", "cloudpickle"])
def test_event_pickle_roundtrip(serializer: ModuleType) -> None:
    """A forksafe.Event must survive (cloud)pickle as a fresh, unset event."""
    event: threading.Event = forksafe.Event()
    event.set()

    data: bytes = serializer.dumps(event)
    restored: threading.Event = serializer.loads(data)

    assert isinstance(restored, forksafe.ResetObject)
    assert restored.is_set() is False
    assert restored in forksafe._resetable_objects


@pytest.mark.parametrize("serializer", [pickle, cloudpickle], ids=["pickle", "cloudpickle"])
def test_global_config_pickle_roundtrip(serializer: ModuleType) -> None:
    """The global ddtrace config (held by every IntegrationConfig) must be
    picklable. It transitively holds a forksafe.Lock via the extra-services
    queue; this is the node that broke Ray Serve cloudpickling (AIPTS-1715).

    As a process-global singleton, it must round-trip to the same instance.
    """
    from ddtrace import config
    from ddtrace.internal.settings._config import Config

    data: bytes = serializer.dumps(config)
    restored: Config = serializer.loads(data)
    assert restored is config


@pytest.mark.parametrize("serializer", [pickle, cloudpickle], ids=["pickle", "cloudpickle"])
def test_integration_config_pickle_roundtrip(serializer: ModuleType) -> None:
    """An IntegrationConfig (e.g. config.fastapi) references the global config
    singleton; pickling it must not drag in (or fail on) the global config's
    process-local lock/file handles (AIPTS-1715).
    """
    from ddtrace import config
    from ddtrace.internal.settings._config import IntegrationConfig

    data: bytes = serializer.dumps(config.fastapi)
    restored: IntegrationConfig = serializer.loads(data)
    assert restored.integration_name == "fastapi"
    assert restored.global_config is config
