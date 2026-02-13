# Utility functions for testing crashtracker in subprocesses
from contextlib import contextmanager
import os
import random
import time
from typing import Callable
from typing import Generator
from typing import Optional

import ddtrace
from tests.utils import TestAgentClient
from tests.utils import TestAgentRequest


def start_crashtracker(port: int, stdout: Optional[str] = None, stderr: Optional[str] = None, tags: dict = {}) -> bool:
    """Start the crashtracker with some placeholder values"""
    ret = False
    try:
        from ddtrace.internal.core import crashtracking
        from ddtrace.internal.settings.crashtracker import config as crashtracker_config

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


def _get_matching_crash_messages(
    test_agent_client: TestAgentClient,
    predicate: Callable[[TestAgentRequest], bool],
    count: int = 1,
    timeout: float = 10.0,
    poll_interval: float = 0.2,
) -> list[TestAgentRequest]:
    """
    Poll the test agent for crash messages matching a predicate.

    Args:
        test_agent_client: The test agent client to poll
        predicate: Function to match desired messages
        count: Number of matching messages to find before returning (default: 1)
        timeout: Maximum time to wait for messages in seconds
        poll_interval: Time between polling attempts in seconds

    Returns:
        list of matching crash messages

    Raises:
        AssertionError: If count matching messages are not found within timeout
    """
    seen_report_ids = set()
    matching_messages: list[TestAgentRequest] = []
    end_time = time.time() + timeout

    while time.time() < end_time:
        incoming_messages = test_agent_client.crash_messages()
        for message in incoming_messages:
            body = message.get("body", b"")
            if isinstance(body, str):
                body = body.encode("utf-8")
            report_id = (hash(body), frozenset(message.get("headers", {}).items()))
            if report_id not in seen_report_ids:
                seen_report_ids.add(report_id)
                if predicate(message):
                    matching_messages.append(message)
                    if len(matching_messages) >= count:
                        return matching_messages

        time.sleep(poll_interval)

    assert len(matching_messages) >= count, (
        f"Expected {count} matching message(s), got {len(matching_messages)} within {timeout}s"
    )
    return matching_messages


def get_crash_report(test_agent_client: TestAgentClient) -> TestAgentRequest:
    """Wait for a crash report from the crashtracker listener socket."""

    def is_crash_report(msg: TestAgentRequest) -> bool:
        return b'"level":"ERROR"' in msg["body"]

    messages = _get_matching_crash_messages(test_agent_client, predicate=is_crash_report)
    return messages[0]


def get_crash_ping(test_agent_client: TestAgentClient) -> TestAgentRequest:
    """Wait for a crash ping from the crashtracker listener socket."""

    def is_crash_ping(msg: TestAgentRequest) -> bool:
        return b'"level":"DEBUG"' in msg["body"]

    messages = _get_matching_crash_messages(test_agent_client, predicate=is_crash_ping)
    return messages[0]


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
