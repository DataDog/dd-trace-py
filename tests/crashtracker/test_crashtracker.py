import os
import shutil
import sys
import warnings

import pytest

import tests.crashtracker.utils as utils


# Crashtracking tests intentionally fork after initializing ddtrace, which spawns worker
# threads; Python 3.12 now emits a DeprecationWarning for that sequence, so ignore it to
# keep stderr assertions stable (mirrors telemetry tests)
warnings.filterwarnings("ignore", category=DeprecationWarning)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_available():
    from ddtrace.internal.core import crashtracking

    assert crashtracking.is_available


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_config():
    import pytest

    from tests.crashtracker.utils import CrashtrackerWrapper

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
    from ddtrace.internal.settings.crashtracker import config as crashtracker_config
    from tests.crashtracker.utils import read_files

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
    from tests.crashtracker.utils import CrashtrackerWrapper

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
def test_crashtracker_receiver_not_in_path():
    import os
    import shutil

    import pytest

    from ddtrace.internal.core import crashtracking
    from tests.crashtracker.utils import CrashtrackerWrapper

    try:
        # Remove the receiver from the path. This mimics the case where we don't
        # have _dd_crashtracker_receiver in the PATH, for example when running
        # in an injected environment. And we should just load the script
        # directly.
        os.environ["PATH"] = ""
        dd_crashtracker_receiver = shutil.which("_dd_crashtracker_receiver")
        assert dd_crashtracker_receiver is None

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
    # 5. Verifies that the crashtracker sends a crash ping to the server
    # 6. Verifies that the crashtracker sends a crash report to the server
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    import ctypes
    import os

    import tests.crashtracker.utils as utils

    with utils.with_test_agent() as client:
        ct = utils.CrashtrackerWrapper(base_name="simple")

        # Fork happens after ddtrace started threads; see warning suppression note above.
        pid = os.fork()
        if pid == 0:
            assert ct.start()
            stdout_msg, stderr_msg = ct.logs()
            assert not stdout_msg, stdout_msg
            assert not stderr_msg, stderr_msg

            ctypes.string_at(0)
            sys.exit(-1)

        # Part 5, Check for the crash ping
        _ping = utils.get_crash_ping(client, service=ct.service)

        # Part 6, Check to see if the listening socket was triggered, if so accept the connection
        # then check to see if the resulting connection is readable
        report = utils.get_crash_report(client, service=ct.service)
        # The crash came from string_at.  Since the over-the-wire format is multipart, chunked HTTP,
        # just check for the presence of the raw string 'string_at' in the response.
        assert b"string_at" in report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_simple_fork():
    # This is similar to the simple test, except crashtracker initialization is done
    # in the parent
    import ctypes
    import os
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import tests.crashtracker.utils as utils

    # Part 1 and 2
    with utils.with_test_agent() as client:
        # Part 3, setup crashtracker in parent
        ct = utils.CrashtrackerWrapper(base_name="simple_fork")
        assert ct.start()
        stdout_msg, stderr_msg = ct.logs()
        assert not stdout_msg
        assert not stderr_msg

        # Part 4, Fork and crash
        # Fork happens after ddtrace started threads; see warning suppression note above.
        pid = os.fork()
        if pid == 0:
            ctypes.string_at(0)
            sys.exit(-1)  # just in case

        # Part 5, check for crash ping
        _ping = utils.get_crash_ping(client, service=ct.service)

        # Part 6, check for crash report
        report = utils.get_crash_report(client, service=ct.service)
        assert b"string_at" in report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_simple_sigbus():
    # This is similar to the simple fork test, except instead of raising a SIGSEGV,
    # it organically raises a real SIGBUS.
    import ctypes
    from ctypes.util import find_library
    import os
    import tempfile
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import tests.crashtracker.utils as utils

    # Part 0, set up the interface to mmap.  We don't want to use mmap.mmap because it has too much protection.
    libc = ctypes.CDLL(find_library("c"))
    assert libc
    libc.mmap.argtypes = [ctypes.c_void_p, ctypes.c_size_t, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_long]
    libc.mmap.restype = ctypes.c_void_p
    PROT_WRITE = ctypes.c_int(0x2)  # Maybe there's a better way to get this constant?
    MAP_PRIVATE = ctypes.c_int(0x02)

    with utils.with_test_agent() as client:
        # Part 3, setup crashtracker in parent
        ct = utils.CrashtrackerWrapper(base_name="simple_sigbus")
        assert ct.start()
        stdout_msg, stderr_msg = ct.logs()
        assert not stdout_msg, stdout_msg
        assert not stderr_msg, stderr_msg

        # Part 4, Fork and crash
        # Fork happens after ddtrace started threads; see warning suppression note above.
        pid = os.fork()
        if pid == 0:
            with tempfile.TemporaryFile() as tmp_file:
                tmp_file.write(b"aaaaaa")  # write some data to the file
                fd = tmp_file.fileno()  # get the file descriptor
                mm = libc.mmap(
                    ctypes.c_void_p(0),
                    ctypes.c_size_t(4096),
                    PROT_WRITE,
                    MAP_PRIVATE,
                    ctypes.c_int(fd),
                    ctypes.c_long(0),
                )
                assert mm
                assert mm != ctypes.c_void_p(-1).value
                arr_type = ctypes.POINTER(ctypes.c_char * 4096)
                arr = ctypes.cast(mm, arr_type).contents
                arr[4095] = b"x"  # sigbus
            sys.exit(-1)  # just in case

        # Part 5, check for crash ping
        _ping = utils.get_crash_ping(client, service=ct.service)

        # Part 6, check for crash report
        report = utils.get_crash_report(client, service=ct.service)
        assert report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_raise_sigsegv():
    import os
    import signal
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import tests.crashtracker.utils as utils

    with utils.with_test_agent() as client:
        ct = utils.CrashtrackerWrapper(base_name="raise_sigsegv")
        assert ct.start()
        stdout_msg, stderr_msg = ct.logs()
        assert not stdout_msg
        assert not stderr_msg

        # Part 4, raise SIGSEGV
        # Fork happens after ddtrace started threads; see warning suppression note above.
        pid = os.fork()
        if pid == 0:
            os.kill(os.getpid(), signal.SIGSEGV.value)
            sys.exit(-1)

        # Part 5, check for crash ping
        _ping = utils.get_crash_ping(client, service=ct.service)

        # Part 6, check for crash report
        report = utils.get_crash_report(client, service=ct.service)
        assert b"os_kill" in report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_raise_sigbus():
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)
    import os
    import signal

    import tests.crashtracker.utils as utils

    with utils.with_test_agent() as client:
        ct = utils.CrashtrackerWrapper(base_name="raise_sigbus")
        assert ct.start()
        stdout_msg, stderr_msg = ct.logs()
        assert not stdout_msg
        assert not stderr_msg

        # Part 4, raise SIGBUS
        # Fork happens after ddtrace started threads; see warning suppression note above.
        pid = os.fork()
        if pid == 0:
            os.kill(os.getpid(), signal.SIGBUS.value)
            sys.exit(-1)

        # Part 5, check for crash ping
        _ping = utils.get_crash_ping(client, service=ct.service)

        # Part 6, check for crash report
        report = utils.get_crash_report(client, service=ct.service)
        assert b"os_kill" in report["body"]


preload_code = """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

import ctypes
import sys
ctypes.string_at(0)
sys.exit(-1)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_preload_default(ddtrace_run_python_code_in_subprocess):
    # Call the program
    service = "test_crashtracker_preload_default"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service
        stdout, stderr, exitcode, _ = ddtrace_run_python_code_in_subprocess(preload_code, env=env)

        # Check for expected exit condition
        assert not stdout
        assert not stderr
        assert exitcode == -11  # exit code for SIGSEGV

        # Part 5, check for crash ping
        _ping = utils.get_crash_ping(client, service=service)

        # Part 6, check for crash report
        report = utils.get_crash_report(client, service=service)
        assert b"string_at" in report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_preload_disabled(ddtrace_run_python_code_in_subprocess):
    # Call the program
    service = "test_crashtracker_preload_disabled"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service
        env["DD_CRASHTRACKING_ENABLED"] = "false"
        stdout, stderr, exitcode, _ = ddtrace_run_python_code_in_subprocess(preload_code, env=env)

        # Check for expected exit condition
        assert not stdout
        assert not stderr
        assert exitcode == -11

        # No crash reports should be sent. Since other tests might be sending reports
        # to the same agent, we check that no report with our service name was sent.
        assert not any(service.encode() in msg["body"] for msg in client.crash_messages())


auto_code = """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

import ctypes
import ddtrace.auto
ctypes.string_at(0)
sys.exit(-1)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_auto_default(run_python_code_in_subprocess):
    # Call the program
    service = "test_crashtracker_auto_default"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service
        stdout, stderr, exitcode, _ = run_python_code_in_subprocess(auto_code, env=env)

        # Check for expected exit condition
        assert not stdout
        assert not stderr
        assert exitcode == -11

        # Part 5, check for crash ping
        _ping = utils.get_crash_ping(client, service=service)

        # Part 6, check for crash report
        report = utils.get_crash_report(client, service=service)
        assert b"string_at" in report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_auto_nostack(run_python_code_in_subprocess):
    import json

    # Call the program
    service = "test_crashtracker_auto_nostack"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service
        env["DD_CRASHTRACKING_STACKTRACE_RESOLVER"] = "none"
        stdout, stderr, exitcode, _ = run_python_code_in_subprocess(auto_code, env=env)

        # Check for expected exit condition
        assert not stdout
        assert not stderr
        # ctypes.string_at(0) typically produces SIGSEGV (-11) but can produce
        # SIGBUS (-7) on some kernel versions / memory configurations.
        assert exitcode in (-11, -7), f"Expected crash signal, got {exitcode}"

        # Check for crash ping
        _ping = utils.get_crash_ping(client, service=service)

        # Wait for the connection
        report = utils.get_crash_report(client, service=service)

        # Check that we don't have stack in error; we might still have it in runtime_stacks
        body = json.loads(report["body"])
        message = json.loads(body["payload"]["logs"][0]["message"])
        error = message["error"]
        assert "string_at" not in error


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_auto_disabled(run_python_code_in_subprocess):
    # Call the program
    service = "test_crashtracker_auto_disabled"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service
        env["DD_CRASHTRACKING_ENABLED"] = "false"
        stdout, stderr, exitcode, _ = run_python_code_in_subprocess(auto_code, env=env)

        # Check for expected exit condition
        assert not stdout
        assert not stderr
        assert exitcode == -11

        # No crash reports should be sent. Since other tests might be sending reports
        # to the same agent, we check that no report with our service name was sent.
        assert not any(service.encode() in msg["body"] for msg in client.crash_messages())


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.skipif(sys.version_info < (3, 10), reason="Runtime stacks are only supported on CPython >= 3.10")
def test_crashtracker_runtime_stacktrace_required(run_python_code_in_subprocess):
    import json

    service = "test_crashtracker_runtime_stacktrace_required"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service
        stdout, stderr, exitcode, _ = run_python_code_in_subprocess(auto_code, env=env)

        # Check for expected exit condition
        assert not stdout
        assert not stderr
        assert exitcode == -11

        # Check for crash ping
        _ping = utils.get_crash_ping(client, service=service)

        # Check for crash report
        report = utils.get_crash_report(client, service=service)

        # We should get the experimental field because `string_at` is in both the
        # native frames stacktrace and experimental runtime_stacks field
        body = json.loads(report["body"])
        message = json.loads(body["payload"]["logs"][0]["message"])
        assert "string_at" in json.dumps(message["experimental"])


# Subprocess code for test_crashtracker_native_extension_crash.
#
# Using C++ means the symbol is name-mangled in the binary
# (e.g. _ZN5crash9null_derefEv). The crashtracker must demangle it back to
# "crash::null_deref()"
_native_extension_crash_code = """
import warnings
warnings.filterwarnings("ignore", category=DeprecationWarning)

import ctypes
import os
import subprocess
import sys
import tempfile

_cpp_source = '''
#include <cstdlib>
namespace crash {
    void null_deref() {
        volatile int* p = nullptr;
        *p = 0xDEAD;
    }
}
extern "C" void trigger_crash() { crash::null_deref(); }
'''

_tmpdir = tempfile.mkdtemp()
_src = os.path.join(_tmpdir, "crash_ext.cpp")
_so  = os.path.join(_tmpdir, "crash_ext.so")
with open(_src, "w") as _f:
    _f.write(_cpp_source)

_r = subprocess.run(["g++", "-shared", "-fPIC", "-o", _so, _src], capture_output=True)
if _r.returncode != 0:
    sys.exit(99)  # g++ not available or compilation failed

_lib = ctypes.CDLL(_so)
_lib.trigger_crash.restype  = None
_lib.trigger_crash.argtypes = []

import ddtrace.auto  # starts the crashtracker
_lib.trigger_crash()
sys.exit(-1)
"""


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.skipif(not shutil.which("g++"), reason="g++ required to compile the native extension")
@pytest.mark.skipif(sys.version_info < (3, 10), reason="Runtime stacks are only supported on CPython >= 3.10")
def test_crashtracker_native_extension_crash(run_python_code_in_subprocess):
    import json

    service = "test_crashtracker_native_extension_crash"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service
        stdout, stderr, exitcode, _ = run_python_code_in_subprocess(_native_extension_crash_code, env=env)

        assert not stdout
        assert not stderr
        assert exitcode == -11, f"Expected SIGSEGV (-11), got {exitcode}"

        _ping = utils.get_crash_ping(client)

        report = utils.get_crash_report(client)
        body = json.loads(report["body"])
        message = json.loads(body["payload"]["logs"][0]["message"])

        native_stack = json.dumps(message["error"]["stack"])

        assert "crash::null_deref" in native_stack, native_stack


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_user_tags_envvar(run_python_code_in_subprocess):
    # Call the program
    service = "test_crashtracker_user_tags_envvar"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service

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

        # Check for crash ping
        _ping = utils.get_crash_ping(client, service=service)

        # Check for crash report
        report = utils.get_crash_report(client, service=service)

        # Now check for the tags
        for k, v in tags.items():
            assert k.encode() in report["body"]
            assert v.encode() in report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
def test_crashtracker_set_tag_profiler_config(snapshot_context, run_python_code_in_subprocess):
    service = "test_crashtracker_set_tag_profiler_config"
    with utils.with_test_agent() as client:
        env = os.environ.copy()
        env["DD_SERVICE"] = service
        env["DD_PROFILING_ENABLED"] = "1"
        stdout, stderr, exitcode, _ = run_python_code_in_subprocess(auto_code, env=env)

        assert not stdout
        assert not stderr
        assert exitcode == -11

        # Check for crash ping
        _ping = utils.get_crash_ping(client, service=service)

        # Check for crash report
        report = utils.get_crash_report(client, service=service)
        # Now check for the profiler_config tag
        assert b"profiler_config" in report["body"]
        profiler_config = "stack_v2_lock_mem_heap_exp_dd_CAP1.0_MAXF64"
        assert profiler_config.encode() in report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_user_tags_profiling():
    # Tests tag ingestion in the backend API (which is currently out of profiling)
    import ctypes
    import os
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import tests.crashtracker.utils as utils

    # Define some tags
    tag_prefix = "manganese_oxides"
    tags = {
        tag_prefix + "_tag1": "pyrolusite",
        tag_prefix + "_tag2": "birnessite",
    }

    with utils.with_test_agent() as client:
        ct = utils.CrashtrackerWrapper(base_name="user_tags_profiling", tags=tags)

        # Fork happens after ddtrace started threads; see warning suppression note above.
        pid = os.fork()
        if pid == 0:
            assert ct.start()
            stdout_msg, stderr_msg = ct.logs()
            assert not stdout_msg
            assert not stderr_msg

            ctypes.string_at(0)
            sys.exit(-1)

        # Check for crash ping
        _ping = utils.get_crash_ping(client, service=ct.service)

        # Check for crash report
        report = utils.get_crash_report(client, service=ct.service)
        assert b"string_at" in report["body"]

        # Now check for the tags
        for k, v in tags.items():
            assert k.encode() in report["body"]
            assert v.encode() in report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_user_tags_core():
    # Tests tag ingestion in the core API
    import ctypes
    import os
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import tests.crashtracker.utils as utils

    # Define some tags
    tag_prefix = "manganese_oxides"
    tags = {
        tag_prefix + "_tag1": "pyrolusite",
        tag_prefix + "_tag2": "birnessite",
    }

    with utils.with_test_agent() as client:
        ct = utils.CrashtrackerWrapper(base_name="user_tags_core", tags=tags)

        # Fork happens after ddtrace started threads; see warning suppression note above.
        pid = os.fork()
        if pid == 0:
            assert ct.start()
            stdout_msg, stderr_msg = ct.logs()
            assert not stdout_msg
            assert not stderr_msg

            ctypes.string_at(0)
            sys.exit(-1)

        # Check for crash ping
        _ping = utils.get_crash_ping(client, service=ct.service)

        # Check for crash report
        report = utils.get_crash_report(client, service=ct.service)
        assert b"string_at" in report["body"]

        # Now check for the tags
        for k, v in tags.items():
            assert k.encode() in report["body"]
            assert v.encode() in report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_process_tags():
    # Tests process_tag ingestion in the core API
    import ctypes
    import os
    import sys
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import tests.crashtracker.utils as utils

    with utils.with_test_agent() as client:
        ct = utils.CrashtrackerWrapper(base_name="tags_required")

        # Fork happens after ddtrace started threads; see warning suppression note above.
        pid = os.fork()
        if pid == 0:
            assert ct.start()
            stdout_msg, stderr_msg = ct.logs()
            assert not stdout_msg
            assert not stderr_msg

            ctypes.string_at(0)
            sys.exit(-1)

        # Check for crash ping
        _ping = utils.get_crash_ping(client, service=ct.service)

        # Check for crash report
        report = utils.get_crash_report(client, service=ct.service)
        assert b"string_at" in report["body"]

        # Verify process_tags are present in crash report
        assert "process_tags".encode() in report["body"]


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess(err=None)
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
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import tests.crashtracker.utils as utils

    # Create a port and listen on it
    with utils.with_test_agent():
        # We're going to create a lot of child processes and we're not going to care about whether they successfully
        # send crashtracking data.  Accordingly, we create a file--children will append here if they run into an
        # unwanted condition, and we'll check it at the end.
        err_file = "/tmp/echild_error.log"

        # Set this process as a subreaper, since we want deparented children to be visible to us
        # (this emulates the behavior of a service which is PID 1 in a container)
        utils.set_cerulean_mollusk()

        # Fork, setup crashtracking in the child.
        # The child process emulates a worker fork in the sense that we spawn a number of them in the parent and then
        # do a timed `waitpid()` anticipating ECHILD until they all exit.
        children = []
        for _ in range(5):
            # Fork happens after ddtrace started threads; see warning suppression note above.
            pid = os.fork()
            if pid == 0:
                rand_num = random.randint(0, 999999)
                base_name = f"echild_hang_{rand_num}"
                ct = utils.CrashtrackerWrapper(base_name=base_name)
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

        # Wait for all children to exit.  It shouldn't take more than 5s, so fail if it does.
        timeout = 5  # seconds
        end_time = time.time() + timeout
        while True:
            if time.time() > end_time:
                raise AssertionError("Timed out waiting for children to exit")
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    time.sleep(0.01)  # Avoid busy-wait when no child exited
            except ChildProcessError:
                break


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess(err=None)
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
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import tests.crashtracker.utils as utils

    with utils.with_test_agent():
        err_file = "/tmp/zombie_error.log"

        # Set this process as a subreaper, since we want deparented children to be visible to us
        # (this emulates the behavior of a service which is PID 1 in a container)
        utils.set_cerulean_mollusk()

        # This is a rapid fan-out procedure.  We do a combination of terminations, aborts, segfaults, etc.,
        # hoping to elicit zombies.
        children = []
        for _ in range(5):
            # Fork happens after ddtrace started threads; see warning suppression note above.
            pid = os.fork()
            if pid == 0:
                rand_num = random.randint(0, 999999)
                base_name = f"no_zombies_{rand_num}"
                ct = utils.CrashtrackerWrapper(base_name=base_name)
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

        # Wait for all children to exit.  It shouldn't take more than 5s, so fail if it does.
        timeout = 5  # seconds
        end_time = time.time() + timeout
        while True:
            if time.time() > end_time:
                raise AssertionError("Timed out waiting for children to exit")
            try:
                pid, _ = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    time.sleep(0.01)  # Avoid busy-wait when no child exited
            except ChildProcessError:
                break


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux only")
@pytest.mark.subprocess()
def test_crashtracker_receiver_env_inheritance():
    """
    The receiver is spawned using execve() and doesn't automatically inherit the
    env, so we need to ensure specific env variables are explicitly passed
    """
    import ctypes
    import os
    import warnings

    # Suppress fork() deprecation warning in multi-threaded process (Python 3.12+)
    warnings.filterwarnings("ignore", category=DeprecationWarning)

    import tests.crashtracker.utils as utils

    test_env_key = "DD_CRASHTRACKING_ERRORS_INTAKE_ENABLED"
    test_env_value = "true"
    os.environ[test_env_key] = test_env_value

    with utils.with_test_agent() as client:
        ct = utils.CrashtrackerWrapper(base_name="env_inheritance")

        # Fork happens after ddtrace started threads; see warning suppression note above.
        pid = os.fork()
        if pid == 0:
            assert os.environ.get(test_env_key) == test_env_value

            assert ct.start()
            stdout_msg, stderr_msg = ct.logs()
            assert not stdout_msg, stdout_msg
            assert not stderr_msg, stderr_msg

            ctypes.string_at(0)
            sys.exit(-1)

        # Check for crash ping
        _ping = utils.get_crash_ping(client, service=ct.service)
        # Check for crash report
        report = utils.get_crash_report(client, service=ct.service)
        assert b"string_at" in report["body"]

    # Clean up
    os.environ.pop(test_env_key, None)
