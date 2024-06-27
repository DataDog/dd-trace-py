import sys

import pytest


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_loading():
    try:
        pass
    except Exception:
        import pytest

        pytest.fail("Crashtracker failed to load")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_available():
    import ddtrace.internal.core.crashtracker as crashtracker

    assert crashtracker.is_available


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_config():
    import pytest

    from tests.internal.crashtracker.utils import read_files
    from tests.internal.crashtracker.utils import start_crashtracker

    start_crashtracker(1234)

    stdout_msg, stderr_msg = read_files(["stdout.log", "stderr.log"])
    if stdout_msg or stderr_msg:
        pytest.fail("contents of stdout.log: %s, stderr.log: %s" % (stdout_msg, stderr_msg))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_config_bytes():
    import pytest

    import ddtrace.internal.core.crashtracker as crashtracker
    from tests.internal.crashtracker.utils import read_files

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

    stdout_msg, stderr_msg = read_files(["stdout.log", "stderr.log"])
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

    from tests.internal.crashtracker.utils import conn_to_bytes
    from tests.internal.crashtracker.utils import crashtracker_receiver_bind
    from tests.internal.crashtracker.utils import listen_get_conn
    from tests.internal.crashtracker.utils import start_crashtracker

    # Part 1 and 2
    port, sock = crashtracker_receiver_bind()
    assert port is not None
    assert sock is not None

    # Part 3 and 4, Fork, setup crashtracker, and crash
    pid = os.fork()
    if pid == 0:
        import ctypes

        start_crashtracker(port)
        ctypes.string_at(0)
        pytest.fail("Should not reach here")
        exit(1)  # just in case

    # Part 5
    # Check to see if the listening socket was triggered, if so accept the connection
    # then check to see if the resulting connection is readable
    conn = listen_get_conn(sock)
    assert conn

    data = conn_to_bytes(conn)
    conn.close()
    assert data

    # The crash came from string_at.  Since the over-the-wire format is multipart, chunked HTTP,
    # just check for the presence of the raw string 'string_at' in the response.
    assert b"string_at" in data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_simple_fork():
    # This is similar to the simple test, except crashtracker initialization is done
    # in the parent
    import os

    from tests.internal.crashtracker.utils import conn_to_bytes
    from tests.internal.crashtracker.utils import crashtracker_receiver_bind
    from tests.internal.crashtracker.utils import listen_get_conn
    from tests.internal.crashtracker.utils import start_crashtracker

    # Part 1 and 2
    port, sock = crashtracker_receiver_bind()
    assert port is not None
    assert sock is not None

    # Part 3
    start_crashtracker(port)

    # Part 4, Fork, setup crashtracker, and crash
    pid = os.fork()
    if pid == 0:
        import ctypes

        import pytest

        ctypes.string_at(0)
        pytest.fail("Should not reach here")
        exit(1)  # just in case

    # Part 5
    # Check to see if the listening socket was triggered, if so accept the connection
    # then check to see if the resulting connection is readable
    conn = listen_get_conn(sock)
    assert conn

    data = conn_to_bytes(conn)
    conn.close()
    assert data

    # The crash came from string_at.  Since the over-the-wire format is multipart, chunked HTTP,
    # just check for the presence of the raw string 'string_at' in the response.
    assert b"string_at" in data
