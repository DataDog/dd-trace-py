import os
import re

import pytest


def test_enable(test_agent_session, run_python_code_in_subprocess):
    code = """
import ddtrace # enables telemetry
from ddtrace.internal.service import ServiceStatus

from ddtrace.internal.telemetry import telemetry_writer
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
    code = """
from ddtrace import tracer

span = tracer.trace("test-telemetry")
span.finish()
    """
    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    assert stderr == b""
    # Ensure telemetry events were sent to the agent (snapshot ensures one trace was generated)
    # Note event order is reversed e.g. event[0] is actually the last event
    events = test_agent_session.get_events()

    assert len(events) == 5
    assert events[0]["request_type"] == "app-closing"
    assert events[1]["request_type"] == "app-dependencies-loaded"
    assert events[2]["request_type"] == "app-integrations-change"
    assert events[3]["request_type"] == "generate-metrics"
    assert events[4]["request_type"] == "app-started"


def test_enable_fork(test_agent_session, run_python_code_in_subprocess):
    """assert app-started/app-closing events are only sent in parent process"""
    code = """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

import os

import ddtrace # enables telemetry
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.telemetry import telemetry_writer


if os.fork() == 0:
    # Send multiple started events to confirm none get sent
    telemetry_writer._app_started()
    telemetry_writer._app_started()
    telemetry_writer._app_started()
else:
    # Print the parent process runtime id for validation
    print(get_runtime_id())
    """
    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    assert stderr == b"", stderr

    runtime_id = stdout.strip().decode("utf-8")

    # Validate that one app-closing event was sent and it was queued in the parent process
    app_closing = test_agent_session.get_events("app-closing")
    assert len(app_closing) == 1
    assert app_closing[0]["runtime_id"] == runtime_id

    # Validate that one app-started event was sent and it was queued in the parent process
    app_started = test_agent_session.get_events("app-started")
    assert len(app_started) == 1
    assert app_started[0]["runtime_id"] == runtime_id


def test_enable_fork_heartbeat(test_agent_session, run_python_code_in_subprocess):
    """assert app-heartbeat events are only sent in parent process when no other events are queued"""
    code = """
import warnings
# This test logs the following warning in py3.12:
# This process (pid=402) is multi-threaded, use of fork() may lead to deadlocks in the child
warnings.filterwarnings("ignore", category=DeprecationWarning)

import os

import ddtrace # enables telemetry
from ddtrace.internal.runtime import get_runtime_id

if os.fork() > 0:
    # Print the parent process runtime id for validation
    print(get_runtime_id())

# Heartbeat events are only sent if no other events are queued
from ddtrace.internal.telemetry import telemetry_writer
telemetry_writer.reset_queues()
telemetry_writer.periodic(force_flush=True)
    """
    env = os.environ.copy()
    env["DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED"] = "false"
    # Prevents dependencies loaded event from being generated
    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr
    assert stderr == b"", stderr

    runtime_id = stdout.strip().decode("utf-8")

    # Allow test agent session to capture all heartbeat events
    app_heartbeats = test_agent_session.get_events("app-heartbeat", filter_heartbeats=False)
    assert len(app_heartbeats) > 0
    for hb in app_heartbeats:
        assert hb["runtime_id"] == runtime_id


def test_heartbeat_interval_configuration(run_python_code_in_subprocess):
    """assert that DD_TELEMETRY_HEARTBEAT_INTERVAL config sets the telemetry writer interval"""
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

    env = os.environ.copy()
    env["DD_TELEMETRY_HEARTBEAT_INTERVAL"] = "61"
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

import ddtrace # enables telemetry
import logging
import os

os.fork()
"""
    )

    assert status == 0, err
    assert err == b"", err


def test_app_started_error_handled_exception(test_agent_session, run_python_code_in_subprocess):
    code = """
import logging
logging.basicConfig()

from ddtrace import tracer
from ddtrace.filters import TraceFilter
from ddtrace.settings import _config

_config._telemetry_dependency_collection = False

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

# force app_started call instead of waiting for periodic()
from ddtrace.internal.telemetry import telemetry_writer
telemetry_writer._app_started()
"""
    _, stderr, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, stderr
    assert b"Exception raised in trace filter" in stderr

    events = test_agent_session.get_events("app-started")

    assert len(events) == 1

    app_started_events = [event for event in events if event["request_type"] == "app-started"]
    assert len(app_started_events) == 1
    assert app_started_events[0]["payload"]["error"]["code"] == 1
    assert (
        "error applying processor <__main__.FailingFilture object at"
        in app_started_events[0]["payload"]["error"]["message"]
    )
    pattern = re.compile(
        ".*ddtrace/_trace/processor/__init__.py/__init__.py:[0-9]+: "
        "error applying processor <__main__.FailingFilture object at 0x[0-9a-f]+>"
    )
    assert pattern.match(app_started_events[0]["payload"]["error"]["message"]), app_started_events[0]["payload"][
        "error"
    ]["message"]


def test_register_telemetry_excepthook_after_another_hook(test_agent_session, run_python_code_in_subprocess):
    out, stderr, status, _ = run_python_code_in_subprocess(
        """
import sys

old_exc_hook = sys.excepthook
def pre_ddtrace_exc_hook(exctype, value, traceback):
    print("pre_ddtrace_exc_hook called")
    return old_exc_hook(exctype, value, traceback)

sys.excepthook = pre_ddtrace_exc_hook

import ddtrace
raise Exception('bad_code')
"""
    )
    assert b"pre_ddtrace_exc_hook called" in out
    assert status == 1, stderr
    assert b"bad_code" in stderr
    # Regression test for python3.12 support
    assert b"RuntimeError: can't create new thread at interpreter shutdown" not in stderr
    # Regression test for invalid number of arguments in wrapped exception hook
    assert b"3 positional arguments but 4 were given" not in stderr

    app_starteds = test_agent_session.get_events("app-started")
    assert len(app_starteds) == 1
    # app-started captures unhandled exceptions raised in application code
    assert app_starteds[0]["payload"]["error"]["code"] == 1
    assert re.search(r"test\.py:\d+:\sbad_code$", app_starteds[0]["payload"]["error"]["message"]), app_starteds[0][
        "payload"
    ]["error"]["message"]


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

    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)

    assert status == 0, stderr
    expected_stderr = b"failed to import"
    assert expected_stderr in stderr

    integrations_events = test_agent_session.get_events("app-integrations-change")
    assert len(integrations_events) == 1
    assert (
        integrations_events[0]["payload"]["integrations"][0]["error"]
        == "failed to import ddtrace module 'ddtrace.contrib.sqlite3' when patching on import"
    )

    # Get metric containing the integration error
    integration_error = {}
    metric_events = test_agent_session.get_events("generate-metrics")
    for event in metric_events:
        for metric in event["payload"]["series"]:
            if metric["metric"] == "integration_errors":
                integration_error = metric
                break

    # assert the integration metric has the correct type, count, and tags
    assert integration_error
    assert integration_error["type"] == "count"
    assert integration_error["points"][0][1] == 1
    assert integration_error["tags"] == ["integration_name:sqlite3", "error_type:attributeerror"]


def test_unhandled_integration_error(test_agent_session, ddtrace_run_python_code_in_subprocess):
    code = """
import logging
logging.basicConfig()

import flask
f = flask.Flask("hi")

# Call flask.wsgi_app with an incorrect number of args
f.wsgi_app()
"""

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code)

    assert status == 1, stderr

    assert b"not enough values to unpack (expected 2, got 0)" in stderr, stderr

    events = test_agent_session.get_events()
    assert len(events) > 0
    # Same runtime id is used
    first_runtimeid = events[0]["runtime_id"]
    for event in events:
        assert event["runtime_id"] == first_runtimeid

    app_started_event = [event for event in events if event["request_type"] == "app-started"]
    assert len(app_started_event) == 1
    assert app_started_event[0]["payload"]["error"]["code"] == 1
    assert "ddtrace/contrib/internal/flask/patch.py" in app_started_event[0]["payload"]["error"]["message"]
    assert "not enough values to unpack (expected 2, got 0)" in app_started_event[0]["payload"]["error"]["message"]

    integration_events = [event for event in events if event["request_type"] == "app-integrations-change"]
    integrations = integration_events[0]["payload"]["integrations"]

    (flask_integration,) = [integration for integration in integrations if integration["name"] == "flask"]

    assert flask_integration["enabled"] is True
    assert flask_integration["compatible"] is False
    assert "ddtrace/contrib/internal/flask/patch.py:" in flask_integration["error"]
    assert "not enough values to unpack (expected 2, got 0)" in flask_integration["error"]

    metric_events = [event for event in events if event["request_type"] == "generate-metrics"]

    assert len(metric_events) == 1
    assert metric_events[0]["payload"]["namespace"] == "tracers"
    assert len(metric_events[0]["payload"]["series"]) == 1
    assert metric_events[0]["payload"]["series"][0]["metric"] == "integration_errors"
    assert metric_events[0]["payload"]["series"][0]["type"] == "count"
    assert len(metric_events[0]["payload"]["series"][0]["points"]) == 1
    assert metric_events[0]["payload"]["series"][0]["points"][0][1] == 1
    assert metric_events[0]["payload"]["series"][0]["tags"] == ["integration_name:flask", "error_type:valueerror"]


def test_app_started_with_install_metrics(test_agent_session, run_python_code_in_subprocess):
    env = os.environ.copy()
    env.update(
        {
            "DD_INSTRUMENTATION_INSTALL_ID": "68e75c48-57ca-4a12-adfc-575c4b05fcbe",
            "DD_INSTRUMENTATION_INSTALL_TYPE": "k8s_single_step",
            "DD_INSTRUMENTATION_INSTALL_TIME": "1703188212",
            "_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED": "true",
        }
    )
    # Generate a trace to trigger app-started event
    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace; ddtrace.tracer.trace('s1').finish()", env=env)
    assert status == 0, stderr

    app_started_event = test_agent_session.get_events("app-started")
    assert len(app_started_event) == 1
    assert app_started_event[0]["payload"]["install_signature"] == {
        "install_id": "68e75c48-57ca-4a12-adfc-575c4b05fcbe",
        "install_type": "k8s_single_step",
        "install_time": "1703188212",
    }


def test_instrumentation_telemetry_disabled(test_agent_session, run_python_code_in_subprocess):
    """Ensure no telemetry events are sent when telemetry is disabled"""
    env = os.environ.copy()
    env["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "false"

    code = """
from ddtrace import tracer
# Create a span to start the telemetry writer
tracer.trace("hi").finish()

# We want to import the telemetry module even when telemetry is disabled.
import sys
assert "ddtrace.internal.telemetry" in sys.modules
"""
    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)

    events = test_agent_session.get_events()
    assert len(events) == 0

    assert status == 0, stderr
    assert stderr == b""


# Disable agentless to ensure telemetry is enabled (agentless needs dd-api-key to be set)
@pytest.mark.subprocess(env={"DD_CIVISIBILITY_AGENTLESS_ENABLED": "0"})
def test_installed_excepthook():
    import sys

    # importing ddtrace initializes the telemetry writer and installs the excepthook
    import ddtrace  # noqa: F401

    assert sys.excepthook.__name__ == "_telemetry_excepthook"

    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._enabled is True
    telemetry_writer.uninstall_excepthook()
    assert sys.excepthook.__name__ != "_telemetry_excepthook"
