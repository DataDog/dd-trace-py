import os
import sys

import pytest

import tests.internal.crashtracker.utils as utils


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_available():
    import ddtrace.internal.datadog.profiling.crashtracker as crashtracker

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

    import ddtrace.internal.datadog.profiling.crashtracker as crashtracker
    from tests.internal.crashtracker.utils import read_files

    try:
        crashtracker.set_url(b"http://localhost:1234")
        crashtracker.set_service(b"my_favorite_service")
        crashtracker.set_version(b"v0.0.0.0.0.0.1")
        crashtracker.set_runtime(b"shmython")
        crashtracker.set_runtime_version(b"v9001")
        crashtracker.set_runtime_id(b"0")
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
def test_crashtracker_started():
    import pytest

    import ddtrace.internal.datadog.profiling.crashtracker as crashtracker
    from tests.internal.crashtracker.utils import read_files

    try:
        crashtracker.set_stdout_filename("stdout.log")
        crashtracker.set_stderr_filename("stderr.log")
        assert crashtracker.start()
        assert crashtracker.is_started()
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
    import ctypes
    import os

    import tests.internal.crashtracker.utils as utils

    # Part 1 and 2
    port, sock = utils.crashtracker_receiver_bind()
    assert port
    assert sock

    # Part 3 and 4, Fork, setup crashtracker, and crash
    pid = os.fork()
    if pid == 0:
        assert utils.start_crashtracker(port)
        stdout_msg, stderr_msg = utils.read_files(["stdout.log", "stderr.log"])
        assert not stdout_msg
        assert not stderr_msg

        ctypes.string_at(0)
        exit(-1)

    # Part 5
    # Check to see if the listening socket was triggered, if so accept the connection
    # then check to see if the resulting connection is readable
    conn = utils.listen_get_conn(sock)
    assert conn

    # The crash came from string_at.  Since the over-the-wire format is multipart, chunked HTTP,
    # just check for the presence of the raw string 'string_at' in the response.
    data = utils.conn_to_bytes(conn)
    conn.close()
    assert b"string_at" in data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_simple_fork():
    # This is similar to the simple test, except crashtracker initialization is done
    # in the parent
    import ctypes
    import os

    import tests.internal.crashtracker.utils as utils

    # Part 1 and 2
    port, sock = utils.crashtracker_receiver_bind()
    assert port
    assert sock

    # Part 3, setup crashtracker in parent
    assert utils.start_crashtracker(port)
    stdout_msg, stderr_msg = utils.read_files(["stdout.log", "stderr.log"])
    assert not stdout_msg
    assert not stderr_msg

    # Part 4, Fork and crash
    pid = os.fork()
    if pid == 0:
        ctypes.string_at(0)
        exit(-1)  # just in case

    # Part 5, check
    conn = utils.listen_get_conn(sock)
    assert conn

    data = utils.conn_to_bytes(conn)
    conn.close()
    assert b"string_at" in data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_simple_sigbus():
    # This is similar to the simple fork test, except instead of raising a SIGSEGV,
    # it organically raises a real SIGBUS.
    import ctypes
    from ctypes.util import find_library
    import os
    import tempfile

    import tests.internal.crashtracker.utils as utils

    # Part 0, set up the interface to mmap.  We don't want to use mmap.mmap because it has too much protection.
    libc = ctypes.CDLL(find_library("c"))
    assert libc
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
    libc.mmap.restype = ctypes.c_void_p
    PROT_WRITE = ctypes.c_int(0x2)  # Maybe there's a better way to get this constant?
    MAP_PRIVATE = ctypes.c_int(0x02)

    # Part 1 and 2
    port, sock = utils.crashtracker_receiver_bind()
    assert port
    assert sock

    # Part 3, setup crashtracker in parent
    assert utils.start_crashtracker(port)
    stdout_msg, stderr_msg = utils.read_files(["stdout.log", "stderr.log"])
    assert not stdout_msg
    assert not stderr_msg

    # Part 4, Fork and crash
    pid = os.fork()
    if pid == 0:
        with tempfile.TemporaryFile() as tmp_file:
            tmp_file.write(b"aaaaaa")  # write some data to the file
            fd = tmp_file.fileno()  # get the file descriptor
            mm = libc.mmap(
                ctypes.c_void_p(0), ctypes.c_size_t(4096), PROT_WRITE, MAP_PRIVATE, ctypes.c_int(fd), ctypes.c_long(0)
            )
            assert mm
            assert mm != ctypes.c_void_p(-1).value
            arr_type = ctypes.POINTER(ctypes.c_char * 4096)
            arr = ctypes.cast(mm, arr_type).contents
            arr[4095] = b"x"  # sigbus
        exit(-1)  # just in case

    # Part 5, check
    conn = utils.listen_get_conn(sock)
    assert conn

    data = utils.conn_to_bytes(conn)
    conn.close()
    assert data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_raise_sigsegv():
    import os
    import signal

    import tests.internal.crashtracker.utils as utils

    # Part 1 and 2
    port, sock = utils.crashtracker_receiver_bind()
    assert port
    assert sock

    assert utils.start_crashtracker(port)
    stdout_msg, stderr_msg = utils.read_files(["stdout.log", "stderr.log"])
    assert not stdout_msg
    assert not stderr_msg

    # Part 4, raise SIGSEGV
    pid = os.fork()
    if pid == 0:
        os.kill(os.getpid(), signal.SIGSEGV.value)
        exit(-1)

    # Part 5, check
    conn = utils.listen_get_conn(sock)
    assert conn

    data = utils.conn_to_bytes(conn)
    conn.close()
    assert b"os_kill" in data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_raise_sigbus():
    import os
    import signal

    import tests.internal.crashtracker.utils as utils

    # Part 1 and 2
    port, sock = utils.crashtracker_receiver_bind()
    assert port
    assert sock

    assert utils.start_crashtracker(port)
    stdout_msg, stderr_msg = utils.read_files(["stdout.log", "stderr.log"])
    assert not stdout_msg
    assert not stderr_msg

    # Part 4, raise SIGBUS
    pid = os.fork()
    if pid == 0:
        os.kill(os.getpid(), signal.SIGBUS.value)
        exit(-1)

    # Part 5, check
    conn = utils.listen_get_conn(sock)
    assert conn

    data = utils.conn_to_bytes(conn)
    conn.close()
    assert b"os_kill" in data


preload_code = """
import ctypes
ctypes.string_at(0)
exit(-1)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_preload_default(ddtrace_run_python_code_in_subprocess):
    # Setup the listening socket before we open ddtrace
    port, sock = utils.crashtracker_receiver_bind()
    assert sock

    # Call the program
    env = os.environ.copy()
    env["DD_TRACE_AGENT_URL"] = "http://localhost:%d" % port
    stdout, stderr, exitcode, _ = ddtrace_run_python_code_in_subprocess(preload_code, env=env)

    # Check for expected exit condition
    assert not stdout
    assert not stderr
    assert exitcode == -11  # exit code for SIGSEGV

    # Wait for the connection
    conn = utils.listen_get_conn(sock)
    assert conn
    data = utils.conn_to_bytes(conn)
    conn.close()
    assert data
    assert b"string_at" in data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_preload_disabled(ddtrace_run_python_code_in_subprocess):
    # Setup the listening socket before we open ddtrace
    port, sock = utils.crashtracker_receiver_bind()
    assert sock

    # Call the program
    env = os.environ.copy()
    env["DD_TRACE_AGENT_URL"] = "http://localhost:%d" % port
    env["DD_CRASHTRACKING_ENABLED"] = "false"
    stdout, stderr, exitcode, _ = ddtrace_run_python_code_in_subprocess(preload_code, env=env)

    # Check for expected exit condition
    assert not stdout
    assert not stderr
    assert exitcode == -11

    # Wait for the connection, which should fail
    conn = utils.listen_get_conn(sock)
    assert not conn


auto_code = """
import ctypes
import ddtrace.auto
ctypes.string_at(0)
exit(-1)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_auto_default(run_python_code_in_subprocess):
    # Setup the listening socket before we open ddtrace
    port, sock = utils.crashtracker_receiver_bind()
    assert sock

    # Call the program
    env = os.environ.copy()
    env["DD_TRACE_AGENT_URL"] = "http://localhost:%d" % port
    stdout, stderr, exitcode, _ = run_python_code_in_subprocess(auto_code, env=env)

    # Check for expected exit condition
    assert not stdout
    assert not stderr
    assert exitcode == -11

    # Wait for the connection
    conn = utils.listen_get_conn(sock)
    assert conn
    data = utils.conn_to_bytes(conn)
    conn.close()
    assert data
    assert b"string_at" in data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_auto_nostack(run_python_code_in_subprocess):
    # Setup the listening socket before we open ddtrace
    port, sock = utils.crashtracker_receiver_bind()
    assert sock

    # Call the program
    env = os.environ.copy()
    env["DD_TRACE_AGENT_URL"] = "http://localhost:%d" % port
    env["DD_CRASHTRACKING_STACKTRACE_RESOLVER"] = "none"
    stdout, stderr, exitcode, _ = run_python_code_in_subprocess(auto_code, env=env)

    # Check for expected exit condition
    assert not stdout
    assert not stderr
    assert exitcode == -11

    # Wait for the connection
    conn = utils.listen_get_conn(sock)
    assert conn
    data = utils.conn_to_bytes(conn)
    conn.close()
    assert data
    assert b"string_at" not in data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_auto_disabled(run_python_code_in_subprocess):
    # Setup the listening socket before we open ddtrace
    port, sock = utils.crashtracker_receiver_bind()
    assert sock

    # Call the program
    env = os.environ.copy()
    env["DD_TRACE_AGENT_URL"] = "http://localhost:%d" % port
    env["DD_CRASHTRACKING_ENABLED"] = "false"
    stdout, stderr, exitcode, _ = run_python_code_in_subprocess(auto_code, env=env)

    # Check for expected exit condition
    assert not stdout
    assert not stderr
    assert exitcode == -11

    # Wait for the connection, which should fail
    conn = utils.listen_get_conn(sock)
    assert not conn


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_tags_required():
    # Tests tag ingestion in the core API
    import ctypes
    import os

    import tests.internal.crashtracker.utils as utils

    port, sock = utils.crashtracker_receiver_bind()
    assert port
    assert sock

    pid = os.fork()
    if pid == 0:
        assert utils.start_crashtracker(port)
        stdout_msg, stderr_msg = utils.read_files(["stdout.log", "stderr.log"])
        assert not stdout_msg
        assert not stderr_msg

        ctypes.string_at(0)
        exit(-1)

    conn = utils.listen_get_conn(sock)
    assert conn
    data = utils.conn_to_bytes(conn)
    conn.close()
    assert b"string_at" in data

    # Now check for the tags
    tags = {
        "is_crash": "true",
        "severity": "crash",
    }
    for k, v in tags.items():
        assert k.encode() in data, k
        assert v.encode() in data, v


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_user_tags_envvar(run_python_code_in_subprocess):
    # Setup the listening socket before we open ddtrace
    port, sock = utils.crashtracker_receiver_bind()
    assert sock

    # Call the program
    env = os.environ.copy()
    env["DD_TRACE_AGENT_URL"] = "http://localhost:%d" % port

    # Injecting tags, but since the way we validate them is with a raw-data string search, we make things unique
    tag_prefix = "cryptocrystalline"
    tags = {
        tag_prefix + "_tag1": "quartz_flint",
        tag_prefix + "_tag2": "quartz_chert",
    }
    env["DD_CRASHTRACKING_TAGS"] = ",".join(["%s:%s" % (k, v) for k, v in tags.items()])
    stdout, stderr, exitcode, _ = run_python_code_in_subprocess(auto_code, env=env)

    # Check for expected exit condition
    assert not stdout
    assert not stderr
    assert exitcode == -11

    # Wait for the connection
    conn = utils.listen_get_conn(sock)
    assert conn
    data = utils.conn_to_bytes(conn)
    assert data

    # Now check for the tags
    for k, v in tags.items():
        assert k.encode() in data
        assert v.encode() in data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_user_tags_profiling():
    # Tests tag ingestion in the backend API (which is currently out of profiling)
    import ctypes
    import os

    import ddtrace.internal.datadog.profiling.crashtracker as crashtracker
    import tests.internal.crashtracker.utils as utils

    # Define some tags
    tag_prefix = "manganese_oxides"
    tags = {
        tag_prefix + "_tag1": "pyrolusite",
        tag_prefix + "_tag2": "birnessite",
    }

    port, sock = utils.crashtracker_receiver_bind()
    assert port
    assert sock

    pid = os.fork()
    if pid == 0:
        # Set the tags before starting
        for k, v in tags.items():
            crashtracker.set_tag(k, v)
        assert utils.start_crashtracker(port)
        stdout_msg, stderr_msg = utils.read_files(["stdout.log", "stderr.log"])
        assert not stdout_msg
        assert not stderr_msg

        ctypes.string_at(0)
        exit(-1)

    conn = utils.listen_get_conn(sock)
    assert conn
    data = utils.conn_to_bytes(conn)
    conn.close()
    assert b"string_at" in data

    # Now check for the tags
    for k, v in tags.items():
        assert k.encode() in data
        assert v.encode() in data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_user_tags_core():
    # Tests tag ingestion in the core API
    import ctypes
    import os

    from ddtrace.internal.core import crashtracking
    import tests.internal.crashtracker.utils as utils

    # Define some tags
    tag_prefix = "manganese_oxides"
    tags = {
        tag_prefix + "_tag1": "pyrolusite",
        tag_prefix + "_tag2": "birnessite",
    }

    port, sock = utils.crashtracker_receiver_bind()
    assert port
    assert sock

    pid = os.fork()
    if pid == 0:
        # Set the tags before starting
        for k, v in tags.items():
            crashtracking.add_tag(k, v)
        assert utils.start_crashtracker(port)
        stdout_msg, stderr_msg = utils.read_files(["stdout.log", "stderr.log"])
        assert not stdout_msg
        assert not stderr_msg

        ctypes.string_at(0)
        exit(-1)

    conn = utils.listen_get_conn(sock)
    assert conn
    data = utils.conn_to_bytes(conn)
    conn.close()
    assert b"string_at" in data

    # Now check for the tags
    for k, v in tags.items():
        assert k.encode() in data
        assert v.encode() in data
