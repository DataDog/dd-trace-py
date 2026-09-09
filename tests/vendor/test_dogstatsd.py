import pytest

from ddtrace.internal.logger import log_filter
from ddtrace.vendor.dogstatsd.base import log


def test_dogstatsd_logger():
    """Ensure dogstatsd logger is initialized as a rate limited logger"""
    assert log_filter in log.filters


@pytest.mark.subprocess
def test_dogstatsd_socket_reset_after_fork():
    """Regression test for GH-19120: a forked child must never reuse the parent's
    cached socket (or its fd number), since the parent can later close and the OS
    can recycle that fd for something unrelated in the child.
    """
    import os

    from ddtrace.vendor.dogstatsd import DogStatsd

    statsd = DogStatsd(host="127.0.0.1", port=9, disable_buffering=True)
    parent_socket = statsd.get_socket()
    parent_fd = parent_socket.fileno()

    r, w = os.pipe()
    pid = os.fork()

    if pid == 0:
        os.close(r)
        child_socket = statsd.get_socket()
        result = "same" if (child_socket is parent_socket or child_socket.fileno() == parent_fd) else "reset"
        os.write(w, result.encode())
        os.close(w)
        os._exit(0)

    os.close(w)
    _, status = os.waitpid(pid, 0)
    result = os.read(r, 16).decode()
    os.close(r)

    assert os.WEXITSTATUS(status) == 0
    assert result == "reset", "child inherited the parent's cached dogstatsd socket/fd instead of resetting it"


@pytest.mark.subprocess
def test_dogstatsd_uds_socket_reset_after_fork():
    """Regression test for GH-19120, UDS transport: the fix must also cover
    socket_path (Unix domain socket) connections, not just UDP.
    """
    import os
    import socket
    import tempfile

    from ddtrace.vendor.dogstatsd import DogStatsd

    uds_path = os.path.join(tempfile.mkdtemp(prefix="ddtrace-uds-"), "statsd.sock")
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
    listener.bind(uds_path)

    statsd = DogStatsd(socket_path=uds_path, disable_buffering=True)
    parent_socket = statsd.get_socket()
    parent_fd = parent_socket.fileno()

    r, w = os.pipe()
    pid = os.fork()

    if pid == 0:
        os.close(r)
        child_socket = statsd.get_socket()
        result = "same" if (child_socket is parent_socket or child_socket.fileno() == parent_fd) else "reset"
        os.write(w, result.encode())
        os.close(w)
        os._exit(0)

    os.close(w)
    _, status = os.waitpid(pid, 0)
    result = os.read(r, 16).decode()
    os.close(r)
    listener.close()

    assert os.WEXITSTATUS(status) == 0
    assert result == "reset", "child inherited the parent's cached UDS socket/fd instead of resetting it"


@pytest.mark.subprocess
def test_dogstatsd_telemetry_socket_reset_after_fork():
    """Regression test for GH-19120, dedicated telemetry destination: the fix must
    also cover self.telemetry_socket, not just self.socket.
    """
    import os

    from ddtrace.vendor.dogstatsd import DogStatsd

    statsd = DogStatsd(
        host="127.0.0.1",
        port=9,
        telemetry_host="127.0.0.1",
        telemetry_port=10,
        disable_buffering=True,
    )
    parent_socket = statsd.get_socket(telemetry=True)
    parent_fd = parent_socket.fileno()

    r, w = os.pipe()
    pid = os.fork()

    if pid == 0:
        os.close(r)
        child_socket = statsd.get_socket(telemetry=True)
        result = "same" if (child_socket is parent_socket or child_socket.fileno() == parent_fd) else "reset"
        os.write(w, result.encode())
        os.close(w)
        os._exit(0)

    os.close(w)
    _, status = os.waitpid(pid, 0)
    result = os.read(r, 16).decode()
    os.close(r)

    assert os.WEXITSTATUS(status) == 0
    assert result == "reset", "child inherited the parent's cached telemetry socket/fd instead of resetting it"


@pytest.mark.subprocess
def test_dogstatsd_socket_lock_not_deadlocked_after_fork():
    """Regression test for GH-19120's lock half: if another thread holds
    _socket_lock at the moment of fork(), the child must not inherit it as
    permanently locked (fork only clones the calling thread, so no thread
    remains alive in the child to release an inherited lock).
    """
    import os
    import threading
    import warnings

    from ddtrace.vendor.dogstatsd import DogStatsd

    # This test deliberately forks while a second thread is alive to exercise the
    # lock-inheritance scenario; suppress CPython's expected multi-threaded-fork
    # DeprecationWarning so it doesn't trip this marker's strict stderr check.
    warnings.filterwarnings("ignore", category=DeprecationWarning, message=".*fork.*")

    statsd = DogStatsd(host="127.0.0.1", port=9, disable_buffering=True)
    statsd.get_socket()

    lock_acquired = threading.Event()
    release_lock = threading.Event()

    def holder():
        with statsd._socket_lock:
            lock_acquired.set()
            release_lock.wait(timeout=5)

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    assert lock_acquired.wait(timeout=5), "helper thread never acquired _socket_lock"

    r, w = os.pipe()
    pid = os.fork()

    if pid == 0:
        os.close(r)
        # A bounded timeout here means a regression fails the test instead of
        # hanging the whole suite forever.
        acquired = statsd._socket_lock.acquire(timeout=5)
        os.write(w, b"acquired" if acquired else b"deadlock")
        os.close(w)
        os._exit(0)

    os.close(w)
    _, status = os.waitpid(pid, 0)
    result = os.read(r, 16).decode()
    os.close(r)
    release_lock.set()
    t.join(timeout=5)

    assert os.WEXITSTATUS(status) == 0
    assert result == "acquired", "child inherited _socket_lock in a permanently locked state"
