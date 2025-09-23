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


def start_crashtracker(port: int, stdout: Optional[str] = None, stderr: Optional[str] = None, tags: dict = {}) -> bool:
    """Start the crashtracker with some placeholder values"""
    ret = False
    try:
        from ddtrace.internal.core import crashtracking
        from ddtrace.settings.crashtracker import config as crashtracker_config

        crashtracker_config.debug_url = "http://localhost:%d" % port
        crashtracker_config.stdout_filename = stdout
        crashtracker_config.stderr_filename = stderr
        crashtracker_config.resolve_frames = "full"

        tags.update(
            {
                "service": "my_favorite_service",
                "version": "v0.0.0.0.0.0.1",
                "runtime": "shmython",
                "runtime_version": "v9001",
                "runtime_id": "0",
                "library_version": "v2.7.1.8",
            }
        )

        ret = crashtracking.start(tags)
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

    def __init__(self, port: int = 9126, base_name="", tags={}):
        if CrashtrackerWrapper._seed == 0:
            CrashtrackerWrapper._seed = random.randint(0, 999999)

        self.port = port
        self.stdout = f"stdout.{base_name}.{CrashtrackerWrapper._seed}.log"
        self.stderr = f"stderr.{base_name}.{CrashtrackerWrapper._seed}.log"
        self.tags = tags

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
        return start_crashtracker(self.port, self.stdout, self.stderr, self.tags)

    def logs(self):
        return read_files([self.stdout, self.stderr])


def get_all_crash_messages(test_agent_client: TestAgentClient) -> List[TestAgentRequest]:
    """
    A test helper to get *all* crash messages is necessary, because crash pings and crash reports
    are sent through async network requests, so we don't have a guarantee of the order they are received.
    We differentiate between crash pings and crash reports downstream
    """
    seen_report_ids = set()
    crash_messages = []
    # 5 iterations * 0.2 second = 1 second total should be enough to get ping + report
    for _ in range(5):
        incoming_messages = test_agent_client.crash_messages()
        for message in incoming_messages:
            body = message.get("body", b"")
            if isinstance(body, str):
                body = body.encode("utf-8")
            report_id = (hash(body), frozenset(message.get("headers", {}).items()))
            if report_id not in seen_report_ids:
                seen_report_ids.add(report_id)
                crash_messages.append(message)

            # If we have both crash ping and crash report (2 reports), we can return early
            if len(crash_messages) >= 2:
                return crash_messages
        time.sleep(0.2)

    return crash_messages


def get_crash_report(test_agent_client: TestAgentClient) -> TestAgentRequest:
    """Wait for a crash report from the crashtracker listener socket."""
    crash_messages = get_all_crash_messages(test_agent_client)
    # We want at least the crash report
    assert len(crash_messages) == 2, f"Expected at least 2 messages; got {len(crash_messages)}"

    # Find the crash report (the one with "is_crash":"true")
    crash_report = None
    for message in crash_messages:
        if b"is_crash:true" in message["body"]:
            crash_report = message
            break

    assert crash_report is not None, "Could not find crash report with 'is_crash:true' tag"
    return crash_report


def get_crash_ping(test_agent_client: TestAgentClient) -> TestAgentRequest:
    """Wait for a crash report from the crashtracker listener socket."""
    crash_messages = get_all_crash_messages(test_agent_client)
    assert len(crash_messages) == 2, f"Expected at least 2 messages; got {len(crash_messages)}"

    # Find the crash ping (the one with "is_crash_ping":"true")
    crash_ping = None
    for message in crash_messages:
        if b"is_crash_ping:true" in message["body"]:
            crash_ping = message
            break

    assert crash_ping is not None, "Could not find crash ping with 'is_crash_ping:true' tag"
    return crash_ping


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
