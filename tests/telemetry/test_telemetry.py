import os

import httpretty
import pytest

from ddtrace.internal import telemetry
from ddtrace.internal.service import ServiceStatus


@pytest.fixture
def telemetry_writer():
    yield telemetry.telemetry_writer


<<<<<<< HEAD
@pytest.fixture(autouse=True)
def mock_send_request(telemetry_writer):
    with httpretty.enabled():
        httpretty.register_uri(httpretty.POST, telemetry_writer.url, status=200)
        yield


def test_enable(telemetry_writer):
    telemetry_writer.enable()
    assert telemetry_writer.status == ServiceStatus.RUNNING

    # assert that calling enable twice does not raise an exception
    telemetry_writer.enable()
    assert telemetry_writer.status == ServiceStatus.RUNNING
=======
def test_enable_fork(test_agent_session, run_python_code_in_subprocess):
    """assert app-started/app-closing/app-heartbeat events are only sent in parent process"""
    code = """
import os
import mock
import time
>>>>>>> ef81a03a (test(telemetry): fix broken fork test (#4063))

    # send queued events
    telemetry_writer.periodic()
    # ensure an app-started telemetry request is sent
    assert len(httpretty.latest_requests()) == 1
    headers = httpretty.last_request().headers
    assert "DD-Telemetry-Request-Type" in headers
    assert headers["DD-Telemetry-Request-Type"] == "app-started"


def test_disable(telemetry_writer):
    telemetry_writer.disable()
    assert telemetry_writer.status == ServiceStatus.STOPPED

<<<<<<< HEAD
    # assert that calling disable twice does not raise an exception
    telemetry_writer.disable()
    assert telemetry_writer.status == ServiceStatus.STOPPED
=======
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
>>>>>>> ef81a03a (test(telemetry): fix broken fork test (#4063))

    # send queued events
    telemetry_writer.periodic()
    # ensure no events were sent
    assert len(httpretty.latest_requests()) == 0


def test_fork():
    telemetry.telemetry_writer.enable()

<<<<<<< HEAD
    pid = os.fork()
    if pid > 0:
        assert telemetry.telemetry_writer.status == ServiceStatus.RUNNING
    else:
        assert telemetry.telemetry_writer._forked is True
        assert telemetry.telemetry_writer._integrations_queue == []
        assert telemetry.telemetry_writer._events_queue == []
        assert telemetry.telemetry_writer.status == ServiceStatus.RUNNING
        # Kill the process so it doesn't continue running the rest of the test suite
        os._exit(0)
=======
    # We expect 3 events from the parent process to get sent, but none from the child process
    assert len(requests) == 3
    # Validate that the runtime id sent for every event is the parent processes runtime id
    assert requests[0]["body"]["runtime_id"] == runtime_id
    assert requests[0]["body"]["request_type"] == "app-heartbeat"
    assert requests[1]["body"]["runtime_id"] == runtime_id
    assert requests[1]["body"]["request_type"] == "app-closing"
    assert requests[2]["body"]["runtime_id"] == runtime_id
    assert requests[2]["body"]["request_type"] == "app-started"
>>>>>>> ef81a03a (test(telemetry): fix broken fork test (#4063))


def test_logs_after_fork(ddtrace_run_python_code_in_subprocess):
    # Regression test: telemetry writer should not log an error when a process forks
    _, err, status, _ = ddtrace_run_python_code_in_subprocess(
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
