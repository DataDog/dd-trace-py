# Description: Test the crashtracker module

import sys

import pytest


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_loading():
    try:
        pass
    except Exception:
        pytest.fail("Crashtracker failed to load")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_available():
    import ddtrace.internal.core.crashtracker as crashtracker

    assert crashtracker.is_available


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_config():
    import os

    import pytest

    import ddtrace.internal.core.crashtracker as crashtracker

    try:
        crashtracker.set_url("http://localhost:1234")
        crashtracker.set_service("my_favorite_service")
        crashtracker.set_version("v0.0.0.0.0.0.1")
        crashtracker.set_runtime("4kph")
        crashtracker.set_runtime_version("v3.1.4.1")
        crashtracker.set_library_version("v2.7.1.8")
        crashtracker.set_stdout_filename("stdout.log")
        crashtracker.set_stderr_filename("stderr.log")
        crashtracker.set_alt_stack(False)
        crashtracker.set_resolve_frames_full()
        assert crashtracker.start()
    except Exception:
        pytest.fail("Exception when starting crashtracker")

    stdout_msg = ""
    stderr_msg = ""
    if os.path.exists("stdout.log"):
        with open("stdout.log", "r") as f:
            stdout_msg = f.read()
    if os.path.exists("stderr.log"):
        with open("stderr.log", "r") as f:
            stderr_msg = f.read()

    if stdout_msg or stderr_msg:
        pytest.fail("contents of stdout.log: %s, stderr.log: %s" % (stdout_msg, stderr_msg))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_config_bytes():
    import os

    import pytest

    import ddtrace.internal.core.crashtracker as crashtracker

    try:
        crashtracker.set_url(b"http://localhost:1234")
        crashtracker.set_service(b"my_favorite_service")
        crashtracker.set_version(b"v0.0.0.0.0.0.1")
        crashtracker.set_runtime(b"4kph")
        crashtracker.set_runtime_version(b"v3.1.4.1")
        crashtracker.set_library_version(b"v2.7.1.8")
        crashtracker.set_stdout_filename(b"stdout.log")
        crashtracker.set_stderr_filename(b"stderr.log")
        crashtracker.set_alt_stack(False)
        crashtracker.set_resolve_frames_full()
        assert crashtracker.start()
    except Exception:
        pytest.fail("Exception when starting crashtracker")

    stdout_msg = ""
    stderr_msg = ""
    if os.path.exists("stdout.log"):
        with open("stdout.log", "r") as f:
            stdout_msg = f.read()
    if os.path.exists("stderr.log"):
        with open("stderr.log", "r") as f:
            stderr_msg = f.read()

    if stdout_msg or stderr_msg:
        pytest.fail("contents of stdout.log: %s, stderr.log: %s" % (stdout_msg, stderr_msg))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_simple():
    # This test does the following
    # 1. Finds a random port in the range 10000-20000 it can bind to (5 retries)
    # 2. Listens on that port for new connections
    # 3. Starts the crashtracker with the URL set to the port
    # 4. Crashes the process
    # 5. Verifies that the crashtracker sends a crash report to the server

    import os
    import random
    import select
    import socket

    # Part 1 and 2
    port = None
    sock = None
    for i in range(5):
        port = 10000
        port += random.randint(0, 9999)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("localhost", port))
            sock.listen(1)
            break
        except Exception:
            port = None
            sock = None

    assert port is not None
    assert sock is not None

    # Part 3 and 4, Fork, setup crashtracker, and crash
    pid = os.fork()
    if pid == 0:
        import ctypes

        import ddtrace.internal.core.crashtracker as crashtracker

        crashtracker.set_url("http://localhost:%d" % port)
        crashtracker.set_service("my_favorite_service")
        crashtracker.set_version("v0.0.0.0.0.0.1")
        crashtracker.set_runtime("4kph")
        crashtracker.set_runtime_version("v3.1.4.1")
        crashtracker.set_library_version("v2.7.1.8")
        crashtracker.set_stdout_filename("stdout.log")
        crashtracker.set_stderr_filename("stderr.log")
        crashtracker.set_alt_stack(False)
        crashtracker.set_resolve_frames_full()
        crashtracker.start()
        ctypes.string_at(0)
        exit(0)  # Should not reach here

    # Part 5
    # Check to see if the listening socket was triggered, if so accept the connection
    # then check to see if the resulting connection is readable
    rlist, _, _ = select.select([sock], [], [], 5.0)  # 5 second timeout
    assert rlist

    conn, _ = sock.accept()
    rlist, _, _ = select.select([conn], [], [], 5.0)
    assert rlist

    data = conn.recv(1024)
    assert data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_simple_fork():
    # This is similar to the simple test, except crashtracker initialization is done
    # in the parent

    import os
    import random
    import select
    import socket

    import ddtrace.internal.core.crashtracker as crashtracker

    # Part 1 and 2
    port = None
    sock = None
    for i in range(5):
        port = 10000
        port += random.randint(0, 9999)
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.bind(("localhost", port))
            sock.listen(1)
            break
        except Exception:
            port = None
            sock = None

    assert port is not None
    assert sock is not None

    # Part 3
    crashtracker.set_url("http://localhost:%d" % port)
    crashtracker.set_service("my_favorite_service")
    crashtracker.set_version("v0.0.0.0.0.0.1")
    crashtracker.set_runtime("4kph")
    crashtracker.set_runtime_version("v3.1.4.1")
    crashtracker.set_library_version("v2.7.1.8")
    crashtracker.set_stdout_filename("stdout.log")
    crashtracker.set_stderr_filename("stderr.log")
    crashtracker.set_alt_stack(False)
    crashtracker.set_resolve_frames_full()
    crashtracker.start()

    # Part 4, Fork, setup crashtracker, and crash
    pid = os.fork()
    if pid == 0:
        import ctypes

        ctypes.string_at(0)
        exit(0)  # Should not reach here

    # Part 5
    # Check to see if the listening socket was triggered, if so accept the connection
    # then check to see if the resulting connection is readable
    rlist, _, _ = select.select([sock], [], [], 5.0)  # 5 second timeout
    assert rlist

    conn, _ = sock.accept()
    rlist, _, _ = select.select([conn], [], [], 5.0)
    assert rlist

    data = conn.recv(1024)
    assert data
