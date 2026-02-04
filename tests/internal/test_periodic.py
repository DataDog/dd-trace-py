import ctypes
import os
import platform
from threading import Event
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


def test_periodic_after_fork_no_restart():
    """Test that PeriodicThread.start() is safe to call after _after_fork().

    This prevents crashes when external libraries (like uvloop) call fork and
    try to restart threads before our forksafe handlers run.
    """

    def _run_periodic():
        pass

    t = periodic.PeriodicThread(0.1, _run_periodic)
    t.start()

    # Simulate what happens in a child process after fork
    t._after_fork()

    # This should not crash or create a new thread
    t.start()  # Should be a no-op

    # Verify thread is marked as after_fork
    assert t._is_after_fork is True


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
