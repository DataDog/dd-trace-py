import os
import re

import pytest
import six


def test_enable(test_agent_session, run_python_code_in_subprocess):
    code = """
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.service import ServiceStatus

telemetry_writer.enable()

assert telemetry_writer.status == ServiceStatus.RUNNING
assert telemetry_writer._worker is not None
"""

    stdout, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 0, stderr
    assert stdout == b"", stderr
    assert stderr == b""


@pytest.mark.snapshot
def test_telemetry_enabled_on_first_tracer_flush(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """assert telemetry events are generated after the first trace is flushed to the agent"""

    # Submit a trace to the agent in a subprocess
    code = 'from ddtrace import tracer; span = tracer.trace("test-telemetry"); span.finish()'
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code)
    assert status == 0, stderr
    assert stderr == b""
    # Ensure telemetry events were sent to the agent (snapshot ensures one trace was generated)
    # Note event order is reversed e.g. event[0] is actually the last event
    events = test_agent_session.get_events()
    assert len(events) == 5
    events.sort(key=lambda x: (x["request_type"], x["seq_id"]), reverse=False)
    assert events[0]["request_type"] == "app-closing"
    assert events[1]["request_type"] == "app-dependencies-loaded"
    assert events[2]["request_type"] == "app-integrations-change"
    assert events[3]["request_type"] == "app-started"
    assert events[4]["request_type"] == "generate-metrics"


def test_enable_fork(test_agent_session, run_python_code_in_subprocess):
    """assert app-started/app-closing events are only sent in parent process"""
    code = """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

import os

from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.telemetry import telemetry_writer

# We have to start before forking since fork hooks are not enabled until after enabling
telemetry_writer.enable()
telemetry_writer._app_started_event()

if os.fork() == 0:
    # Send multiple started events to confirm none get sent
    telemetry_writer._app_started_event()
    telemetry_writer._app_started_event()
    telemetry_writer._app_started_event()
else:
    # Print the parent process runtime id for validation
    print(get_runtime_id())
    """

    stdout, stderr, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, stderr
    assert stderr == b"", stderr

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
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

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
telemetry_writer.periodic(True)
# Disable telemetry writer to avoid sending app-closed event
telemetry_writer.disable()
    """

    stdout, stderr, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, stderr
    assert stderr == b"", stderr

    runtime_id = stdout.strip().decode("utf-8")

    requests = test_agent_session.get_requests()

    # We expect event from the parent process to get sent, but none from the child process
    assert len(requests) == 1
    # Validate that the runtime id sent for every event is the parent processes runtime id
    assert requests[0]["body"]["runtime_id"] == runtime_id
    assert requests[0]["body"]["request_type"] == "app-heartbeat"


def test_heartbeat_interval_configuration(run_python_code_in_subprocess):
    """assert that DD_TELEMETRY_HEARTBEAT_INTERVAL config sets the telemetry writer interval"""
    env = os.environ.copy()
    env["DD_TELEMETRY_HEARTBEAT_INTERVAL"] = "61"
    code = """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

from ddtrace import config
assert config._telemetry_heartbeat_interval == 61

from ddtrace.internal.telemetry import telemetry_writer
assert telemetry_writer._is_periodic is True
assert telemetry_writer.interval == 10
assert telemetry_writer._periodic_threshold == 5
    """

    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    assert stderr == b""


def test_logs_after_fork(run_python_code_in_subprocess):
    # Regression test: telemetry writer should not log an error when a process forks
    _, err, status, _ = run_python_code_in_subprocess(
        """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

import ddtrace
import logging
import os

logging.basicConfig() # required for python 2.7
ddtrace.internal.telemetry.telemetry_writer.enable()
os.fork()
""",
    )

    assert status == 0, err
    assert err == b"", err


def test_app_started_error_handled_exception(test_agent_session, run_python_code_in_subprocess):
    code = """
import logging
logging.basicConfig()

from ddtrace import tracer
from ddtrace.filters import TraceFilter

class FailingFilture(TraceFilter):
    def process_trace(self, trace):
       raise Exception("Exception raised in trace filter")

tracer.configure(
    settings={
        "FILTERS": [FailingFilture()],
    }
)

# generate and encode span
tracer.trace("hello").finish()
"""
    _, stderr, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, stderr
    assert b"Exception raised in trace filter" in stderr

    events = test_agent_session.get_events()

    assert len(events) == 4

    # Same runtime id is used
    assert events[0]["runtime_id"] == events[1]["runtime_id"]

    app_started_events = [event for event in events if event["request_type"] == "app-started"]
    assert len(app_started_events) == 1
    assert app_started_events[0]["payload"]["error"]["code"] == 1
    assert "error applying processor FailingFilture()" in app_started_events[0]["payload"]["error"]["message"]
    pattern = re.compile(
        ".*ddtrace/internal/processor/trace.py/trace.py:[0-9]+: error applying processor FailingFilture()"
    )
    assert pattern.match(app_started_events[0]["payload"]["error"]["message"]), app_started_events[0]["payload"][
        "error"
    ]["message"]


def test_app_started_error_unhandled_exception(test_agent_session, run_python_code_in_subprocess):
    env = os.environ.copy()
    env["DD_SPAN_SAMPLING_RULES"] = "invalid_rules"
    env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "true"

    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace", env=env)
    assert status == 1, stderr
    assert b"Unable to parse DD_SPAN_SAMPLING_RULES=" in stderr

    events = test_agent_session.get_events()

    assert len(events) == 3

    # Same runtime id is used
    assert events[0]["runtime_id"] == events[1]["runtime_id"]
    assert events[0]["request_type"] == "app-closing"
    assert events[1]["request_type"] == "app-dependencies-loaded"
    assert events[2]["request_type"] == "app-started"
    assert events[2]["payload"]["error"]["code"] == 1

    assert "ddtrace/internal/sampling.py" in events[2]["payload"]["error"]["message"]
    assert "Unable to parse DD_SPAN_SAMPLING_RULES='invalid_rules'" in events[2]["payload"]["error"]["message"]


def test_handled_integration_error(test_agent_session, run_python_code_in_subprocess):
    code = """
import logging
logging.basicConfig()

import sqlite3
# patch() of the sqlite integration assumes this attribute is there
# removing it should cause patching to fail.
del sqlite3.connect

from ddtrace import patch, tracer
patch(raise_errors=False, sqlite3=True)

# Create a span to start the telemetry writer
tracer.trace("hi").finish()
"""

    _, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 0, stderr
    expected_stderr = b"failed to import"
    assert expected_stderr in stderr

    events = test_agent_session.get_events()

    assert len(events) > 1
    for event in events:
        # Same runtime id is used
        assert event["runtime_id"] == events[0]["runtime_id"]

    integrations_events = [event for event in events if event["request_type"] == "app-integrations-change"]

    assert len(integrations_events) == 1
    assert (
        integrations_events[0]["payload"]["integrations"][0]["error"]
        == "failed to import ddtrace module 'ddtrace.contrib.sqlite3' when patching on import"
    )

    metric_events = [
        event
        for event in events
        if event["request_type"] == "generate-metrics"
        and event["payload"]["series"][0]["metric"] == "integration_errors"
    ]
    assert len(metric_events) == 1
    assert len(metric_events[0]["payload"]["series"]) == 1
    assert metric_events[0]["payload"]["series"][0]["type"] == "count"
    assert len(metric_events[0]["payload"]["series"][0]["points"]) == 1
    assert metric_events[0]["payload"]["series"][0]["points"][0][1] == 1


def test_unhandled_integration_error(test_agent_session, run_python_code_in_subprocess):
    code = """
import logging
logging.basicConfig()

import ddtrace
ddtrace.patch(flask=True)

import flask
f = flask.Flask("hi")

# Call flask.wsgi_app with an incorrect number of args
f.wsgi_app()
"""

    _, stderr, status, _ = run_python_code_in_subprocess(code)

    assert status == 1, stderr

    if six.PY2:
        assert b"ValueError: need more than 0 values to unpack" in stderr, stderr
    else:
        assert b"not enough values to unpack (expected 2, got 0)" in stderr, stderr

    events = test_agent_session.get_events()

    assert len(events) == 5
    # Same runtime id is used
    assert (
        events[0]["runtime_id"]
        == events[1]["runtime_id"]
        == events[2]["runtime_id"]
        == events[3]["runtime_id"]
        == events[4]["runtime_id"]
    )

    app_started_event = [event for event in events if event["request_type"] == "app-started"]
    assert len(app_started_event) == 1
    assert app_started_event[0]["payload"]["error"]["code"] == 1
    assert "ddtrace/contrib/flask/patch.py" in app_started_event[0]["payload"]["error"]["message"]
    if six.PY2:
        assert b"need more than 0 values to unpack" in app_started_event[0]["payload"]["error"]["message"]
    else:
        assert "not enough values to unpack (expected 2, got 0)" in app_started_event[0]["payload"]["error"]["message"]

    integration_events = [event for event in events if event["request_type"] == "app-integrations-change"]
    integrations = integration_events[0]["payload"]["integrations"]
    assert len(integrations) == 1
    assert integrations[0]["name"] == "flask"
    assert integrations[0]["enabled"] is True
    assert integrations[0]["compatible"] is False
    assert "ddtrace/contrib/flask/patch.py:" in integrations[0]["error"]
    if six.PY2:
        assert b"need more than 0 values to unpack" in integrations[0]["error"]
    else:
        assert "not enough values to unpack (expected 2, got 0)" in integrations[0]["error"]

    metric_events = [event for event in events if event["request_type"] == "generate-metrics"]

    assert len(metric_events) == 1
    assert metric_events[0]["payload"]["namespace"] == "tracers"
    assert len(metric_events[0]["payload"]["series"]) == 1
    assert metric_events[0]["payload"]["series"][0]["metric"] == "integration_errors"
    assert metric_events[0]["payload"]["series"][0]["type"] == "count"
    assert len(metric_events[0]["payload"]["series"][0]["points"]) == 1
    assert metric_events[0]["payload"]["series"][0]["points"][0][1] == 1
    assert metric_events[0]["payload"]["series"][0]["tags"] == ["integration_name:flask", "error_type:valueerror"]
