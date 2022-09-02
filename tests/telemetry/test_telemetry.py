<<<<<<< HEAD
=======
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
>>>>>>> fffb0ef2 (chore(telemetry): enable telemetry by default (#4003))
import os

import httpretty
import pytest

from ddtrace.internal import telemetry
from ddtrace.internal.service import ServiceStatus


@pytest.fixture
def telemetry_writer():
    yield telemetry.telemetry_writer


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

    # assert that calling disable twice does not raise an exception
    telemetry_writer.disable()
    assert telemetry_writer.status == ServiceStatus.STOPPED

    # send queued events
    telemetry_writer.periodic()
    # ensure no events were sent
    assert len(httpretty.latest_requests()) == 0


def test_fork():
    telemetry.telemetry_writer.enable()

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
