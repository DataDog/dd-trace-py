# Utility functions for testing crashtracker in subprocesses
from contextlib import contextmanager
import os
import random
import time
from typing import Generator
from typing import List
from typing import Optional

import ddtrace
from tests.utils import TestAgentClient
from tests.utils import TestAgentRequest


def start_crashtracker(port: int, stdout: Optional[str] = None, stderr: Optional[str] = None) -> bool:
    """Start the crashtracker with some placeholder values"""
    ret = False
    try:
        import ddtrace.internal.datadog.profiling.crashtracker as crashtracker

        crashtracker.set_url("http://localhost:%d" % port)
        crashtracker.set_service("my_favorite_service")
        crashtracker.set_version("v0.0.0.0.0.0.1")
        crashtracker.set_runtime("shmython")
        crashtracker.set_runtime_version("v9001")
        crashtracker.set_runtime_id("0")
        crashtracker.set_library_version("v2.7.1.8")
        crashtracker.set_stdout_filename(stdout)
        crashtracker.set_stderr_filename(stderr)
        crashtracker.set_resolve_frames_full()
        ret = crashtracker.start()
    except Exception as e:
        print("Failed to start crashtracker: %s" % str(e))
        ret = False
    return ret


def read_files(files):
    msg = []
    for file in files:
        this_msg = ""
        if os.path.exists(file):
            with open(file, "r") as f:
                this_msg = f.read()
        msg.append(this_msg)
    return msg


def set_cerulean_mollusk():
    """
    Many crashtracking tests deal with the behavior of the init process in a given PID namespace.
    For testing, it's useful to designate a process as the subreaper via `PR_SET_CHILD_SUBREAPER`.
    This function sets the current process as the subreaper.
    There is no need to fear this function.
    """
    try:
        import ctypes
        import ctypes.util

        libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
        PR_SET_CHILD_SUBREAPER = 36  # from <linux/prctl.h>

        # Now setup the prctl definition
        libc.prctl.argtypes = [ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
        libc.prctl.restype = ctypes.c_int

        result = libc.prctl(PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0)
        if result != 0:
            return False
    except Exception as e:
        print("Failed to set subreaper: %s" % str(e))
        return False
    return True


# A class that wraps start_crashtracker and maintains its own logfiles
class CrashtrackerWrapper:
    _seed = 0

    def __init__(self, port: int = 9126, base_name=""):
        if CrashtrackerWrapper._seed == 0:
            CrashtrackerWrapper._seed = random.randint(0, 999999)

        self.port = port
        self.stdout = f"stdout.{base_name}.{CrashtrackerWrapper._seed}.log"
        self.stderr = f"stderr.{base_name}.{CrashtrackerWrapper._seed}.log"

        for file in [self.stdout, self.stderr]:
            if os.path.exists(file):
                os.unlink(file)

    def __del__(self):
        for file in [self.stdout, self.stderr]:
            if os.path.exists(file):
                os.unlink(file)

    def get_filenames(self):
        return [self.stdout, self.stderr]

    def start(self):
        return start_crashtracker(self.port, self.stdout, self.stderr)

    def logs(self):
        return read_files([self.stdout, self.stderr])


def wait_for_crash_reports(test_agent_client: TestAgentClient) -> List[TestAgentRequest]:
    crash_reports = []
    for _ in range(5):
        crash_reports = test_agent_client.crash_reports()
        if crash_reports:
            return crash_reports
        time.sleep(0.1)

    return crash_reports


def get_crash_report(test_agent_client: TestAgentClient) -> TestAgentRequest:
    """Wait for a crash report from the crashtracker listener socket."""
    crash_reports = wait_for_crash_reports(test_agent_client)
    assert len(crash_reports) == 1
    return crash_reports[0]


@contextmanager
def with_test_agent() -> Generator[TestAgentClient, None, None]:
    base_url = ddtrace.tracer.agent_trace_url or "http://localhost:9126"  # default to local test agent
    client = TestAgentClient(base_url=base_url, token=None)
    try:
        # Reset state before starting the test
        client.clear()
        yield client
    finally:
        # Always reset state at the end of the test
        client.clear()
