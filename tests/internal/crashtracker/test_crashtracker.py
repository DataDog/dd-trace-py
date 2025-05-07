import os
import sys

import pytest

import tests.internal.crashtracker.utils as utils


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_available():
    from ddtrace.internal.core import crashtracking

    assert crashtracking.is_available


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_config():
    import pytest

    from tests.internal.crashtracker.utils import CrashtrackerWrapper

    ct = CrashtrackerWrapper(1234, "config")
    assert ct.start()
    stdout_msg, stderr_msg = ct.logs()
    if stdout_msg or stderr_msg:
        pytest.fail("contents of stdout.log: %s, stderr.log: %s" % (stdout_msg, stderr_msg))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_config_bytes():
    import os

    import pytest

    from ddtrace.internal.core import crashtracking
    from ddtrace.settings.crashtracker import config as crashtracker_config
    from tests.internal.crashtracker.utils import read_files

    # Delete the stdout and stderr files if they exist
    base_name = b"config_bytes"
    stdout, stderr = (f"{base_name}.{x}.log" for x in (b"stdout", b"stderr"))
    for file in [stdout, stderr]:
        if os.path.exists(file):
            os.unlink(file)

    try:
        crashtracker_config.debug_url = "http://localhost:1234"
        crashtracker_config.stdout_filename = stdout
        crashtracker_config.stderr_filename = stderr
        crashtracker_config.resolve_frames = "full"

        tags = {
            "service": b"my_favorite_service",
            "version": b"v0.0.0.0.0.0.1",
            "runtime": b"shmython",
            "runtime_version": b"v9001",
            "runtime_id": b"0",
            "library_version": b"v2.7.1.8",
        }

        crashtracking.start(tags)
    except Exception:
        pytest.fail("Exception when starting crashtracker")

    stdout_msg, stderr_msg = read_files([stdout, stderr])
    if stdout_msg or stderr_msg:
        pytest.fail("contents of stdout.log: %s, stderr.log: %s" % (stdout_msg, stderr_msg))


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_started():
    import pytest

    from ddtrace.internal.core import crashtracking
    from tests.internal.crashtracker.utils import CrashtrackerWrapper

    try:
        ct = CrashtrackerWrapper(1234, "started")
        assert ct.start()
        assert crashtracking.is_started()  # Confirmation at the module level
    except Exception:
        pytest.fail("Exception when starting crashtracker")

    stdout_msg, stderr_msg = ct.logs()
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
        ct = utils.CrashtrackerWrapper(port, "simple")
        assert ct.start()
        stdout_msg, stderr_msg = ct.logs()
        assert not stdout_msg, stdout_msg
        assert not stderr_msg, stderr_msg

        ctypes.string_at(0)
        sys.exit(-1)

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
    ct = utils.CrashtrackerWrapper(port, "simple_fork")
    assert ct.start()
    stdout_msg, stderr_msg = ct.logs()
    assert not stdout_msg
    assert not stderr_msg

    # Part 4, Fork and crash
    pid = os.fork()
    if pid == 0:
        ctypes.string_at(0)
        sys.exit(-1)  # just in case

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
    ct = utils.CrashtrackerWrapper(port, "simple_sigbus")
    assert ct.start()
    stdout_msg, stderr_msg = ct.logs()
    assert not stdout_msg, stdout_msg
    assert not stderr_msg, stderr_msg

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
        sys.exit(-1)  # just in case

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

    ct = utils.CrashtrackerWrapper(port, "raise_sigsegv")
    assert ct.start()
    stdout_msg, stderr_msg = ct.logs()
    assert not stdout_msg
    assert not stderr_msg

    # Part 4, raise SIGSEGV
    pid = os.fork()
    if pid == 0:
        os.kill(os.getpid(), signal.SIGSEGV.value)
        sys.exit(-1)

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

    ct = utils.CrashtrackerWrapper(port, "raise_sigbus")
    assert ct.start()
    stdout_msg, stderr_msg = ct.logs()
    assert not stdout_msg
    assert not stderr_msg

    # Part 4, raise SIGBUS
    pid = os.fork()
    if pid == 0:
        os.kill(os.getpid(), signal.SIGBUS.value)
        sys.exit(-1)

    # Part 5, check
    conn = utils.listen_get_conn(sock)
    assert conn

    data = utils.conn_to_bytes(conn)
    conn.close()
    assert b"os_kill" in data


preload_code = """
import ctypes
import sys
ctypes.string_at(0)
sys.exit(-1)
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
sys.exit(-1)
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
        ct = utils.CrashtrackerWrapper(port, "tags_required")
        assert ct.start()
        stdout_msg, stderr_msg = ct.logs()
        assert not stdout_msg
        assert not stderr_msg

        ctypes.string_at(0)
        sys.exit(-1)

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
@pytest.mark.skipif(sys.version_info >= (3, 13), reason="Fails on 3.13")
def test_crashtracker_set_tag_profiler_config(run_python_code_in_subprocess):
    port, sock = utils.crashtracker_receiver_bind()
    assert sock

    env = os.environ.copy()
    env["DD_TRACE_AGENT_URL"] = "http://localhost:%d" % port
    env["DD_PROFILING_ENABLED"] = "1"
    stdout, stderr, exitcode, _ = run_python_code_in_subprocess(auto_code, env=env)

    assert not stdout
    assert not stderr
    assert exitcode == -11

    # Wait for the connection
    conn = utils.listen_get_conn(sock)
    assert conn
    data = utils.conn_to_bytes(conn)
    assert data

    # Now check for the profiler_config tag
    assert b"profiler_config" in data
    profiler_config = "stack_v2_lock_mem_heap_exp_dd_CAP1.0_MAXF64"
    assert profiler_config.encode() in data


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_user_tags_profiling():
    # Tests tag ingestion in the backend API (which is currently out of profiling)
    import ctypes
    import os

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
        ct = utils.CrashtrackerWrapper(port, "user_tags_profiling", tags)
        assert ct.start()
        stdout_msg, stderr_msg = ct.logs()
        assert not stdout_msg
        assert not stderr_msg

        ctypes.string_at(0)
        sys.exit(-1)

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
        ct = utils.CrashtrackerWrapper(port, "user_tags_core", tags)
        assert ct.start()
        stdout_msg, stderr_msg = ct.logs()
        assert not stdout_msg
        assert not stderr_msg

        ctypes.string_at(0)
        sys.exit(-1)

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
def test_crashtracker_echild_hang():
    """
    It's possible for user code and services to harvest child processes by doing a `waitpid()` until errno is ECHILD.
    Although this is a more common pattern for native code, because the crashtracking receiver could suppress this
    condition, we test for it.
    """
    import ctypes
    import os
    import random
    import sys
    import time

    import tests.internal.crashtracker.utils as utils

    # Create a port and listen on it
    port, sock = utils.crashtracker_receiver_bind()
    assert port
    assert sock

    # We're going to create a lot of child processes and we're not going to care about whether they successfully send
    # crashtracking data.  Accordingly, we create a file--children will append here if they run into an unwanted
    # condition, and we'll check it at the end.
    err_file = "/tmp/echild_error.log"

    # Set this process as a subreaper, since we want deparented children to be visible to us
    # (this emulates the behavior of a service which is PID 1 in a container)
    utils.set_cerulean_mollusk()

    # Fork, setup crashtracking in the child.
    # The child process emulates a worker fork in the sense that we spawn a number of them in the parent and then
    # do a timed `waitpid()` anticipating ECHILD until they all exit.
    children = []
    for _ in range(5):
        pid = os.fork()
        if pid == 0:
            rand_num = random.randint(0, 999999)
            base_name = f"echild_hang_{rand_num}"
            ct = utils.CrashtrackerWrapper(port, base_name)
            if not ct.start():
                with open(err_file, "a") as f:
                    f.write("X")
                    sys.exit(-1)

            stdout_msg, stderr_msg = ct.logs()
            if not stdout_msg or not stderr_msg:
                with open(err_file, "a") as f:
                    f.write("X")
                    sys.exit(-1)

            # Crashtracking is started.  Let's sleep for 100ms to give the parent a chance to do some stuff,
            # then crash.
            time.sleep(0.1)

            ctypes.string_at(0)
            sys.exit(-1)
        else:
            children.append(pid)

    # Wait for all children to exit.  It shouldn't take more than 1s, so fail if it does.
    timeout = 1  # seconds
    end_time = time.time() + timeout
    while True:
        if time.time() > end_time:
            pytest.fail("Timed out waiting for children to exit")
        try:
            _, __ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        except Exception as e:
            pytest.fail("Unexpected exception: %s" % e)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_no_zombies():
    """
    If a process has been designated as the reaper for another process (either because it is the parent, it is marked
    as the init process for the given PID namespace, or it has been set as a subreaper), then it is responsible for
    harvesting the return status of its children.  If this is not done, then the entry is never removed from the
    process table for the terminated PID.  This is often not a problem, but we should still avoid unbounded resource
    leaks.
    """
    import ctypes
    import os
    import random
    import sys
    import time

    import tests.internal.crashtracker.utils as utils

    # Create a port and listen on it
    # This doesn't matter, but we don't need spurious errors in crashtracking now.
    port, sock = utils.crashtracker_receiver_bind()
    assert port
    assert sock

    err_file = "/tmp/zombie_error.log"

    # Set this process as a subreaper, since we want deparented children to be visible to us
    # (this emulates the behavior of a service which is PID 1 in a container)
    utils.set_cerulean_mollusk()

    # This is a rapid fan-out procedure.  We do a combination of terminations, aborts, segfaults, etc., hoping to elicit
    # zombies.
    children = []
    for _ in range(5):
        pid = os.fork()
        if pid == 0:
            rand_num = random.randint(0, 999999)
            base_name = f"no_zombies_{rand_num}"
            ct = utils.CrashtrackerWrapper(port, base_name)
            if not ct.start():
                with open(err_file, "a") as f:
                    f.write("X")
                    sys.exit(-1)

            stdout_msg, stderr_msg = ct.logs()
            if not stdout_msg or not stderr_msg:
                with open(err_file, "a") as f:
                    f.write("X")
                    sys.exit(-1)

            # Crashtracking is started.  Let's sleep for 100ms to give the parent a chance to do some stuff,
            # then crash.
            time.sleep(0.1)

            ctypes.string_at(0)
            sys.exit(-1)
        else:
            children.append(pid)

    # Wait for all children to exit.  It shouldn't take more than 1s, so fail if it does.
    timeout = 1  # seconds
    end_time = time.time() + timeout
    while True:
        if time.time() > end_time:
            pytest.fail("Timed out waiting for children to exit")
        try:
            _, __ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        except Exception as e:
            pytest.fail("Unexpected exception: %s" % e)
