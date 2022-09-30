import os

import pytest


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


@pytest.mark.snapshot
def test_telemetry_enabled_on_first_tracer_flush(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """assert telemetry events are generated after the first trace is flushed to the agent"""
    # Using ddtrace-run and/or importing ddtrace alone should not enable telemetry
    # Telemetry data should only be sent after the first trace to the agent
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess("import ddtrace")
    assert status == 0, stderr
    # No trace and No Telemetry
    assert len(test_agent_session.get_events()) == 0

    # Submit a trace to the agent in a subprocess
    code = 'from ddtrace import tracer; span = tracer.trace("test-telemetry"); span.finish()'
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code)
    assert status == 0, stderr
    assert stderr == b""
    # Ensure telemetry events were sent to the agent (snapshot ensures one trace was generated)
    events = test_agent_session.get_events()
    assert len(events) == 2
    assert events[0]["request_type"] == "app-closing"
    assert events[1]["request_type"] == "app-started"


def test_enable_fork(test_agent_session, run_python_code_in_subprocess):
    """assert app-started/app-closing events are only sent in parent process"""
    code = """
import os

from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.telemetry import telemetry_writer

# We have to start before forking since fork hooks are not enabled until after enabling
telemetry_writer.enable()

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

    # We expect 2 events from the parent process to get sent, but none from the child process
    assert len(requests) == 2
    # Validate that the runtime id sent for every event is the parent processes runtime id
    assert requests[0]["body"]["runtime_id"] == runtime_id
    assert requests[0]["body"]["request_type"] == "app-closing"
    assert requests[1]["body"]["runtime_id"] == runtime_id
    assert requests[1]["body"]["request_type"] == "app-started"


def test_enable_fork_heartbeat(test_agent_session, run_python_code_in_subprocess):
    """assert app-heartbeat events are only sent in parent process when no other events are queued"""
    code = """
import os

from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.telemetry import telemetry_writer

telemetry_writer.enable()
# Reset queue to avoid sending app-started event
telemetry_writer.reset_queues()

if os.fork() > 0:
    # Print the parent process runtime id for validation
    print(get_runtime_id())

# Call periodic to send heartbeat event
telemetry_writer.periodic()
# Disable telemetry writer to avoid sending app-closed event
telemetry_writer.disable()
    """

    stdout, stderr, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, stderr
    assert stderr == b""

    runtime_id = stdout.strip().decode("utf-8")

    requests = test_agent_session.get_requests()

    # We expect event from the parent process to get sent, but none from the child process
    assert len(requests) == 1
    # Validate that the runtime id sent for every event is the parent processes runtime id
    assert requests[0]["body"]["runtime_id"] == runtime_id
    assert requests[0]["body"]["request_type"] == "app-heartbeat"


def test_heartbeat_interval_configuration(run_python_code_in_subprocess):
    """assert that DD_TELEMETRY_HEARTBEAT_INTERVAL config sets the telemetry writer interval"""
    heartbeat_interval = "1.5"
    env = os.environ.copy()
    env["DD_TELEMETRY_HEARTBEAT_INTERVAL"] = heartbeat_interval
    code = """
from ddtrace.internal.telemetry import telemetry_writer
assert telemetry_writer.interval == {}
    """.format(
        heartbeat_interval
    )

    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    assert stderr == b""


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
