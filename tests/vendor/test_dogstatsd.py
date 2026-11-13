from ddtrace.internal.logger import log_filter
from ddtrace.vendor.dogstatsd.base import log


def test_dogstatsd_logger():
    """Ensure dogstatsd logger is initialized as a rate limited logger"""
    assert log_filter in log.filters


def test_dogstatsd_socket_not_inherited_after_fork():
    """A forked child must not reuse the parent's cached socket (and its fd number)."""
    import os

    from ddtrace.vendor.dogstatsd.base import DogStatsd

    client = DogStatsd(host="127.0.0.1", port=9, disable_buffering=True)
    parent_socket = client.get_socket()
    assert client.socket is parent_socket

    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            os.write(w, b"1" if client.socket is None else b"0")
        finally:
            os._exit(0)
    os.close(w)
    result = os.read(r, 1)
    os.close(r)
    os.waitpid(pid, 0)

    assert result == b"1", "child inherited the parent's DogStatsd socket"
    # The parent keeps using its own socket.
    assert client.socket is parent_socket
    assert parent_socket.fileno() != -1
    client.close_socket()


def test_dogstatsd_socket_lock_not_held_after_fork():
    """A lock held by another thread at fork time must not stay locked in the child."""
    import os
    import threading

    from ddtrace.vendor.dogstatsd.base import DogStatsd

    client = DogStatsd(host="127.0.0.1", port=9, disable_buffering=True)
    held = threading.Event()
    release = threading.Event()

    def hold():
        with client._socket_lock:
            held.set()
            release.wait(10)

    t = threading.Thread(target=hold)
    t.start()
    held.wait(10)
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        try:
            ok = client._socket_lock.acquire(timeout=2)
            os.write(w, b"1" if ok else b"0")
        finally:
            os._exit(0)
    os.close(w)
    result = os.read(r, 1)
    os.close(r)
    release.set()
    t.join()
    os.waitpid(pid, 0)
    assert result == b"1", "child inherited a locked _socket_lock"
