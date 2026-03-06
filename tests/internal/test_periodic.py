import ctypes
import os
import platform
from threading import Event
from threading import Thread
from time import monotonic
from time import sleep

import pytest

from ddtrace.internal import periodic
from ddtrace.internal import service


def test_periodic():
    x = {"OK": False}

    thread_started = Event()
    thread_continue = Event()

    def _run_periodic():
        thread_started.set()
        x["OK"] = True
        thread_continue.wait()

    def _on_shutdown():
        x["DOWN"] = True

    t = periodic.PeriodicThread(0.001, _run_periodic, on_shutdown=_on_shutdown)
    t.start()
    thread_started.wait()
    thread_continue.set()
    t.stop()
    t.join()
    assert x["OK"]
    assert x["DOWN"]


def test_periodic_double_start():
    def _run_periodic():
        pass

    t = periodic.PeriodicThread(0.1, _run_periodic)
    t.start()
    with pytest.raises(RuntimeError):
        t.start()
    t.stop()
    t.join()


def test_periodic_error():
    x = {"OK": False}

    thread_started = Event()
    thread_continue = Event()

    def _run_periodic():
        thread_started.set()
        thread_continue.wait()
        raise ValueError

    def _on_shutdown():
        x["DOWN"] = True

    t = periodic.PeriodicThread(0.001, _run_periodic, on_shutdown=_on_shutdown)
    t.start()
    thread_started.wait()
    thread_continue.set()
    t.stop()
    t.join()
    assert "DOWN" not in x


def test_periodic_service_start_stop():
    t = periodic.PeriodicService(1)
    t.start()
    with pytest.raises(service.ServiceStatusError):
        t.start()
    t.stop()
    t.join()
    with pytest.raises(service.ServiceStatusError):
        t.stop()
    with pytest.raises(service.ServiceStatusError):
        t.stop()
    t.join()
    t.join()


def test_periodic_join_stop_no_start():
    t = periodic.PeriodicService(1)
    t.join()
    with pytest.raises(service.ServiceStatusError):
        t.stop()
    t.join()
    t = periodic.PeriodicService(1)
    with pytest.raises(service.ServiceStatusError):
        t.stop()
    t.join()
    with pytest.raises(service.ServiceStatusError):
        t.stop()


def test_awakeable_periodic_service():
    queue = []

    class AwakeMe(periodic.AwakeablePeriodicService):
        def periodic(self):
            queue.append(len(queue))

    interval = 1

    awake_me = AwakeMe(interval)

    awake_me.start()

    # Manually awake the service
    n = 10
    for _ in range(10):
        awake_me.awake()

    assert queue == list(range(n))

    # Sleep long enough to also trigger the periodic function with the timeout
    sleep(1.1 * interval)

    awake_me.stop()

    assert queue == list(range(n + 1))


def test_forksafe_awakeable_periodic_service():
    queue = [None]
    periodic_ran = Event()

    class AwakeMe(periodic.ForksafeAwakeablePeriodicService):
        def reset(self):
            queue.clear()
            periodic_ran.clear()

        def periodic(self):
            queue.append(len(queue))
            periodic_ran.set()

    awake_me = AwakeMe(1)
    awake_me.start()

    assert queue

    pid = os.fork()
    if pid == 0:
        # child: check that the thread has been restarted and the state has been
        # reset
        assert not queue

        awake_me.awake()
        periodic_ran.wait(timeout=5)  # Wait for periodic() to complete
        assert queue
        os._exit(42)

    awake_me.stop()

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 42


def test_periodic_service_no_immediate_run_after_fork():
    periodic_ran = Event()

    class EveryMinute(periodic.PeriodicService):
        def periodic(self):
            periodic_ran.set()

    # Use a long interval so periodic() can only run on an explicit wakeup.
    service = EveryMinute(60)
    service.start()

    try:
        assert service._worker is not None

        # Simulate fork stop/restart around the worker.
        service._worker._before_fork()
        service._worker.join()
        service._worker._after_fork()

        # Prefork stop wakeup must not trigger a synthetic run after restart.
        assert not periodic_ran.wait(timeout=0.5)

        # A real wakeup after restart must still run periodic().
        service._worker.awake()
        assert periodic_ran.wait(timeout=1)
    finally:
        service.stop()
        service.join()


def test_periodic_thread_preserves_awake_during_restart_window():
    """Ensure awake() isn't lost while a periodic thread is paused for fork.

    The fork-safe runtime stops periodic threads before fork and restarts them
    afterwards. A stop wakeup from that path should not trigger an immediate
    periodic() run after restart, but a real awake() call made during this
    restart window must still be honored.
    """
    periodic_ran = Event()
    awake_done = Event()

    def _run_periodic():
        periodic_ran.set()

    t = periodic.PeriodicThread(60, _run_periodic)
    t.start()
    t._before_fork()
    t.join()

    awaker = Thread(target=lambda: (t.awake(), awake_done.set()))
    awaker.start()

    # Simulate thread restart after fork in parent.
    t._after_fork()

    assert awake_done.wait(timeout=1), "awake() should complete after restart"
    assert periodic_ran.wait(timeout=1), "periodic() should run for the awake request"

    t.stop()
    t.join()
    awaker.join(timeout=1)


# ---------------------------------------------------------------------------
# Regression tests for PeriodicThread crash scenarios (IR-50207)
#
# These tests reproduce the three crash paths identified in the
# PeriodicThread crash analysis:
#   Problem 1a: TOCTTOU race — crash at interpreter exit
#   Problem 2:  Post-fork mutex corruption
#
# Strategy for reproducing race conditions:
#   - Problem 1a: Many threads with very short intervals constantly cycling
#     through AllowThreads (GIL release/acquire). atexit cleared so threads
#     are alive during Py_FinalizeEx. The threads are in _request->wait()
#     (not in callbacks) so ~AllowThreads fires during finalization.
#   - Problem 2: Many threads with tiny intervals maximizing mutex hold time.
#     Fork rapidly from multiple threads to catch mid-mutex state. Run in
#     child without stopping first.
# ---------------------------------------------------------------------------


@pytest.mark.skipif(platform.system() != "Linux", reason="Crash behavior is Linux/glibc specific")
@pytest.mark.subprocess(
    status=0,
    err=lambda s: "std::terminate" not in s and "Aborted" not in s,
    timeout=15,
)
def test_periodic_thread_no_crash_at_exit():
    """Problem 1a: PeriodicThread must not crash during interpreter exit.

    Creates many threads with instant callbacks and short intervals so they
    constantly cycle through the AllowThreads acquire/release path. Clears
    atexit so threads are alive when Py_FinalizeEx runs. The threads spend
    most of their time in _request->wait() with the GIL released via
    AllowThreads — when the wait times out, ~AllowThreads calls
    PyEval_RestoreThread which can hit the finalization race.

    On unfixed CPython 3.12, this triggers:
      PyEval_RestoreThread -> take_gil -> pthread_exit -> __forced_unwind
      -> std::terminate (SIGABRT)
    """
    import atexit

    from ddtrace.internal._threads import PeriodicThread
    from ddtrace.internal._threads import periodic_threads

    def _fast_callback():
        # Return immediately so the thread goes back to _request->wait()
        # as fast as possible, maximizing time in AllowThreads scope.
        pass

    threads = []
    # Many threads with very short intervals — each one cycles through
    # AllowThreads (GIL release -> wait -> GIL acquire) every 1ms.
    for i in range(20):
        t = PeriodicThread(
            interval=0.001,
            target=_fast_callback,
            name=f"test:ExitRace{i}",
            no_wait_at_start=True,
        )
        t.start()
        threads.append(t)

    # Remove all threads from periodic_threads and clear atexit so
    # nothing stops them before finalization.
    for t in threads:
        periodic_threads.pop(t.ident, None)
    atexit._clear()

    # Exit — 20 threads are cycling through AllowThreads during Py_FinalizeEx.
    # The more threads, the wider the window for the TOCTTOU race.


@pytest.mark.skipif(platform.system() != "Linux", reason="Fork behavior is Linux specific")
@pytest.mark.subprocess(
    status=0,
    err=lambda s: "Segmentation fault" not in s,
    timeout=15,
)
def test_periodic_thread_no_crash_fork_while_active():
    """Problem 2: Fork while PeriodicThread is actively running.

    Runs multiple threads with tiny intervals so they're constantly locking
    and unlocking their internal Event mutexes. Forks rapidly from the main
    thread to catch a thread mid-mutex-operation. In the child, calls
    _after_fork/_after_fork_child which on unfixed code tries .clear() on
    Events with corrupted mutexes -> SIGSEGV.

    Uses multiple concurrent threads and many rapid forks to maximize the
    probability of catching the race.
    """
    import os
    import time

    from ddtrace.internal._threads import PeriodicThread

    def _fast_callback():
        pass

    # Multiple threads with very short intervals — each one constantly
    # cycles through Event::wait/set/clear, holding mutexes briefly.
    threads = []
    for i in range(10):
        t = PeriodicThread(
            interval=0.001,
            target=_fast_callback,
            name=f"test:ForkRace{i}",
            no_wait_at_start=True,
        )
        t.start()
        threads.append(t)

    time.sleep(0.01)

    # Fork rapidly — 50 attempts to catch threads mid-mutex
    for _ in range(50):
        pid = os.fork()
        if pid == 0:
            # Child: threads don't exist, mutexes may be corrupted.
            try:
                for t in threads:
                    if hasattr(t, "_after_fork_child"):
                        t._after_fork_child()
                    else:
                        t._after_fork()
                for t in threads:
                    t.stop()
                    t.join(timeout=1)
            except Exception:
                os._exit(1)
            os._exit(0)
        else:
            _, status = os.waitpid(pid, 0)
            if os.WIFSIGNALED(status):
                for t in threads:
                    t.stop()
                    t.join(timeout=1)
                raise AssertionError(f"Child killed by signal {os.WTERMSIG(status)}")
            exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
            if exit_code != 0:
                for t in threads:
                    t.stop()
                    t.join(timeout=1)
                raise AssertionError(f"Child exited with code {exit_code}")

    for t in threads:
        t.stop()
        t.join(timeout=2)


@pytest.mark.skipif(platform.system() != "Linux", reason="Fork behavior is Linux specific")
@pytest.mark.subprocess(
    status=0,
    err=lambda s: "Segmentation fault" not in s,
    timeout=15,
)
def test_periodic_thread_no_crash_fork_during_start():
    """Problem 2 variant (Vianney's race): Fork while PeriodicThread is starting.

    Races thread creation against fork. PeriodicThread_start blocks in
    _started->wait() with the GIL released via AllowThreads. If fork()
    fires at that moment, the child inherits a locked _started Event mutex.

    Uses multiple fork threads and many iterations to widen the race window.
    """
    import os
    import threading

    from ddtrace.internal._threads import PeriodicThread

    failures = []
    lock = threading.Lock()

    def _callback():
        pass

    def _do_fork():
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        else:
            _, status = os.waitpid(pid, 0)
            if os.WIFSIGNALED(status):
                with lock:
                    failures.append(os.WTERMSIG(status))

    # 50 iterations, each with a fork thread racing against thread start
    for _ in range(50):
        t = PeriodicThread(interval=60, target=_callback, name="test:ForkStart")

        # Launch multiple fork threads to increase collision probability
        fork_threads = []
        for _ in range(3):
            ft = threading.Thread(target=_do_fork)
            ft.start()
            fork_threads.append(ft)

        t.start()
        t.stop()
        t.join(timeout=2)
        for ft in fork_threads:
            ft.join(timeout=2)

    if failures:
        raise AssertionError(f"Child killed by signals: {failures}")


def test_timer():
    end = 0

    class TestTimer(periodic.Timer):
        def timeout(self):
            nonlocal end
            end = monotonic()

    T = 0.1
    t = TestTimer(T)

    start = monotonic()
    t.start()
    t.join()

    assert end >= start + T


def test_timer_reset():
    end = count = 0

    class TestTimer(periodic.Timer):
        def timeout(self):
            nonlocal end, count

            count += 1
            end = monotonic()

    T = 0.1
    t = TestTimer(T)

    start = monotonic()
    t.start()

    for _ in range(N := 5):
        sleep(T / 2)
        t.reset()

    t.join()

    assert count == 1, "timed out once"
    assert end >= start + (N * T / 2) + T


def _get_native_thread_name():
    """Get the native thread name for the current thread.

    Returns None if the platform doesn't support reading thread names or if reading fails.
    """
    system = platform.system()

    if system == "Linux":
        # Read from /proc/self/task/<tid>/comm
        try:
            tid = ctypes.CDLL(None).syscall(186)  # SYS_gettid
            with open(f"/proc/self/task/{tid}/comm", "r") as f:
                return f.read().strip()
        except Exception:
            return None

    elif system == "Darwin":  # macOS
        try:
            # Use pthread_getname_np
            pthread = ctypes.CDLL("/usr/lib/libpthread.dylib")
            pthread_getname_np = pthread.pthread_getname_np
            pthread_getname_np.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_size_t]
            pthread_getname_np.restype = ctypes.c_int

            # Get current thread handle
            pthread_self = pthread.pthread_self
            pthread_self.restype = ctypes.c_void_p

            thread_handle = pthread_self()
            name_buffer = ctypes.create_string_buffer(64)

            if pthread_getname_np(thread_handle, name_buffer, 64) == 0:
                return name_buffer.value.decode("utf-8")
        except Exception:
            return None

    elif system == "Windows":
        # Windows thread naming is more complex and requires Windows 10+
        # For now, skip testing on Windows
        return None

    return None


def test_periodic_thread_naming():
    """Test that native thread names are set correctly with various formats.

    This verifies that the native thread naming functionality works and handles
    truncation properly, prioritizing the class name after the colon separator.
    """
    native_name = [None]
    thread_started = Event()

    def _capture_native_name():
        native_name[0] = _get_native_thread_name()
        thread_started.set()

    system = platform.system()

    # Test 1: Short name (should work on all platforms)
    thread_started.clear()
    native_name[0] = None
    t1 = periodic.PeriodicThread(0.1, _capture_native_name, name="ShortName")
    t1.start()
    thread_started.wait()
    t1.stop()
    t1.join()

    if native_name[0] is not None:
        assert native_name[0] == "ShortName", f"Expected 'ShortName', got '{native_name[0]}'"

    # Test 2: Long name with module:class format
    # On Linux (15 char limit), should keep "StackCollectorThread" -> truncated to "StackCollectorT"
    # On macOS (63 char limit), should keep the full class name "StackCollectorThread"
    thread_started.clear()
    native_name[0] = None
    t2 = periodic.PeriodicThread(0.1, _capture_native_name, name="ddtrace.profiling.collector:StackCollectorThread")
    t2.start()
    thread_started.wait()
    t2.stop()
    t2.join()

    if native_name[0] is not None:
        if system == "Linux":
            # Linux truncates to 15 characters
            assert native_name[0] == "StackCollectorT", f"Expected 'StackCollectorT' on Linux, got '{native_name[0]}'"
        elif system == "Darwin":
            # macOS can fit the full class name
            assert native_name[0].endswith("StackCollectorThread"), (
                f"Expected 'StackCollectorThread' on macOS, got '{native_name[0]}'"
            )

    # Test 3: Long name without colon (should truncate from start)
    thread_started.clear()
    native_name[0] = None
    t3 = periodic.PeriodicThread(0.1, _capture_native_name, name="VeryLongThreadNameWithoutColonSeparator")
    t3.start()
    thread_started.wait()
    t3.stop()
    t3.join()

    if native_name[0] is not None:
        if system == "Linux":
            # Should truncate from the start to 15 characters
            assert native_name[0] == "VeryLongThreadN", f"Expected 'VeryLongThreadN' on Linux, got '{native_name[0]}'"
        elif system == "Darwin":
            # macOS limit is 63, name is 41 chars, should fit fully
            assert native_name[0] == "VeryLongThreadNameWithoutColonSeparator", (
                f"Expected full name on macOS, got '{native_name[0]}'"
            )

    # Test 4: Edge case - name that's exactly at the limit with colon
    # "module:ClassNameFit" on Linux should become "ClassNameFit" (12 chars, fits)
    thread_started.clear()
    native_name[0] = None
    t4 = periodic.PeriodicThread(0.1, _capture_native_name, name="some.module:ClassNameFit")
    t4.start()
    thread_started.wait()
    t4.stop()
    t4.join()

    if native_name[0] is not None:
        # On all platforms, the full name fits within limits (23 chars < 63 on macOS, extracts to 12 chars on Linux)
        assert native_name[0].endswith("ClassNameFit"), (
            f"Expected name ending with 'ClassNameFit', got '{native_name[0]}'"
        )
