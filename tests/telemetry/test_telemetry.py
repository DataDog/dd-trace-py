def test_enable(test_agent_session, run_python_code_in_subprocess):
    code = """
from ddtrace.internal.telemetry import telemetry_writer
telemetry_writer.enable()
"""

    stdout, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 0, stderr
    assert stdout == b"", stderr
    assert stderr == b""

    events = test_agent_session.get_events()
    assert len(events) == 2

    # Same runtime id is used
    assert events[0]["runtime_id"] == events[1]["runtime_id"]
    assert events[0]["request_type"] == "app-closing"
    assert events[1]["request_type"] == "app-started"


def test_enable_fork(test_agent_session, run_python_code_in_subprocess):
    """assert app-started/app-closing/app-heartbeat events are only sent in parent process"""
    code = """
import os
import mock
import time

from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.telemetry import telemetry_writer


# We have to start before forking since fork hooks are not enabled until after enabling
telemetry_writer.enable()

# Update time to generate a heartbeat event when the writer is flushed
now = time.time()
time_skip_for_heartbeat = 2*telemetry_writer.HEARTBEAT_MIN_INTERVAL
time.time = mock.Mock(return_value= now + time_skip_for_heartbeat)

if os.fork() == 0:
    # Send multiple started events to confirm none get sent
    telemetry_writer.app_started_event()
    telemetry_writer.app_started_event()
    telemetry_writer.app_started_event()
else:
    # Print the parent process runtime id for validation
    print(get_runtime_id())
    """

    stdout, stderr, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, stderr
    assert stderr == b""

    runtime_id = stdout.strip().decode("utf-8")

    requests = test_agent_session.get_requests()

    # We expect 3 events from the parent process to get sent, but none from the child process
    assert len(requests) == 3
    # Validate that the runtime id sent for every event is the parent processes runtime id
    assert requests[0]["body"]["runtime_id"] == runtime_id
    assert requests[0]["body"]["request_type"] == "app-heartbeat"
    assert requests[1]["body"]["runtime_id"] == runtime_id
    assert requests[1]["body"]["request_type"] == "app-closing"
    assert requests[2]["body"]["runtime_id"] == runtime_id
    assert requests[2]["body"]["request_type"] == "app-started"


def test_logs_after_fork(run_python_code_in_subprocess):
    # Regression test: telemetry writer should not log an error when a process forks
    _, err, status, _ = run_python_code_in_subprocess(
        """
import ddtrace
import logging
import os

logging.basicConfig() # required for python 2.7
ddtrace.internal.telemetry.telemetry_writer.enable()
os.fork()
""",
    )

    assert status == 0, err
    assert err == b""
