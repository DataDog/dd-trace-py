import os

import pytest

from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
from ddtrace.internal.service import ServiceStatus
from ddtrace.runtime import RuntimeMetrics
from tests.utils import DummyTracer


def test_runtime_metrics_api():
    RuntimeMetrics.enable()
    assert RuntimeWorker._instance is not None
    assert RuntimeWorker._instance.status == ServiceStatus.RUNNING

    RuntimeMetrics.disable()
    assert RuntimeWorker._instance is None


def test_runtime_metrics_api_idempotency():
    RuntimeMetrics.enable()
    instance = RuntimeWorker._instance
    assert instance is not None
    RuntimeMetrics.enable()
    assert RuntimeWorker._instance is instance

    RuntimeMetrics.disable()
    assert RuntimeWorker._instance is None
    RuntimeMetrics.disable()


@pytest.mark.subprocess
def test_manually_start_runtime_metrics():
    """
    When importing and manually starting runtime metrics
        Runtime metrics worker starts and there are no errors
    """
    from ddtrace.runtime import RuntimeMetrics

    RuntimeMetrics.enable()
    assert RuntimeMetrics._enabled

    RuntimeMetrics.disable()
    assert not RuntimeMetrics._enabled


def test_manually_start_runtime_metrics_telemetry(test_agent_session, run_python_code_in_subprocess):
    """
    When importing and manually starting runtime metrics
        Runtime metrics worker starts and we report that it is enabled via telemetry
    """
    code = """
from ddtrace.internal.telemetry import telemetry_writer
telemetry_writer.start()

from ddtrace.runtime import RuntimeMetrics

assert not RuntimeMetrics._enabled
RuntimeMetrics.enable()
assert RuntimeMetrics._enabled

telemetry_writer.stop()
telemetry_writer.join(3)
    """

    def find_telemetry_event(events, request_type):
        e = [e for e in events if e["request_type"] == request_type]
        assert len(e) == 1
        return e[0]

    _, stderr, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, stderr

    events = test_agent_session.get_events()
    # app-started, app-closing, app-client-configuration-change, app-dependencies-loaded
    assert len(events) == 4

    # Note: the initial app-started event is going to say that it is not enabled, because
    #       we only look at the env variable DD_RUNTIME_METRICS_ENABLED to set the initial
    #       value.
    #
    #       This test helps validate that manually enabling runtime metrics via code will
    #       will report the correct config status.
    app_started_event = find_telemetry_event(events, "app-started")
    config_changed_event = find_telemetry_event(events, "app-client-configuration-change")

    runtimemetrics_enabled = [
        c for c in app_started_event["payload"]["configuration"] if c["name"] == "runtimemetrics_enabled"
    ]
    assert len(runtimemetrics_enabled) == 1
    assert runtimemetrics_enabled[0]["value"] is False
    assert runtimemetrics_enabled[0]["origin"] == "unknown"

    config = config_changed_event["payload"]["configuration"]
    assert len(config) == 1
    assert config[0]["name"] == "runtimemetrics_enabled"
    assert config[0]["value"] is True
    assert config[0]["origin"] == "unknown"


def test_manually_stop_runtime_metrics_telemetry(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """
    When importing and manually stopping runtime metrics
        Runtime metrics worker stops and we report that it is enabled via telemetry
    """
    code = """
from ddtrace.internal.telemetry import telemetry_writer
telemetry_writer.start()

from ddtrace.runtime import RuntimeMetrics

assert RuntimeMetrics._enabled
RuntimeMetrics.disable()
assert not RuntimeMetrics._enabled

telemetry_writer.stop()
telemetry_writer.join(3)
    """

    def find_telemetry_event(events, request_type):
        e = [e for e in events if e["request_type"] == request_type]
        assert len(e) == 1
        return e[0]

    env = os.environ.copy()
    env["DD_RUNTIME_METRICS_ENABLED"] = "true"
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    events = test_agent_session.get_events()
    # app-started, app-closing, app-client-configuration-change, app-integrations-change, app-dependencies-loaded
    assert len(events) == 5

    # Note: the initial app-started event is going to say that it is enabled, because we
    #       set DD_RUNTIME_METRICS_ENABLED=true and are using ddtrace-run which will enable
    #       runtime metrics for us.
    #
    #       This test helps validate that manually disabling runtime metrics via code will
    #       will report the correct config status.
    app_started_event = find_telemetry_event(events, "app-started")
    config_changed_event = find_telemetry_event(events, "app-client-configuration-change")

    runtimemetrics_enabled = [
        c for c in app_started_event["payload"]["configuration"] if c["name"] == "runtimemetrics_enabled"
    ]
    assert len(runtimemetrics_enabled) == 1
    assert runtimemetrics_enabled[0]["value"] is True
    assert runtimemetrics_enabled[0]["origin"] == "unknown"

    config = config_changed_event["payload"]["configuration"]
    assert len(config) == 1
    assert config[0]["name"] == "runtimemetrics_enabled"
    assert config[0]["value"] is False
    assert config[0]["origin"] == "unknown"


def test_start_runtime_metrics_via_env_var(monkeypatch, ddtrace_run_python_code_in_subprocess):
    """
    When running with ddtrace-run and DD_RUNTIME_METRICS_ENABLED is set
        Runtime metrics worker starts and there are no errors
    """

    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace.runtime import RuntimeMetrics
assert not RuntimeMetrics._enabled
"""
    )
    assert status == 0

    monkeypatch.setenv("DD_RUNTIME_METRICS_ENABLED", "true")
    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace.runtime import RuntimeMetrics
assert RuntimeMetrics._enabled
""",
    )
    assert status == 0


def test_runtime_metrics_via_env_var_manual_start(monkeypatch, ddtrace_run_python_code_in_subprocess):
    """
    When running with ddtrace-run and DD_RUNTIME_METRICS_ENABLED is set and trying to start RuntimeMetrics manually
        Runtime metrics worker starts and there are no errors
    """

    monkeypatch.setenv("DD_RUNTIME_METRICS_ENABLED", "true")
    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace.runtime import RuntimeMetrics
assert RuntimeMetrics._enabled
RuntimeMetrics.enable()
assert RuntimeMetrics._enabled
""",
    )
    assert status == 0


@pytest.mark.parametrize(
    "enable_kwargs",
    (
        dict(),
        dict(tracer=DummyTracer(), dogstatsd_url="udp://agent:8125"),
        dict(tracer=DummyTracer(), dogstatsd_url="udp://agent:8125", flush_interval=100.0),
        dict(flush_interval=0),
    ),
)
def test_runtime_metrics_enable(enable_kwargs):
    try:
        RuntimeMetrics.enable(**enable_kwargs)

        assert RuntimeWorker._instance is not None
        assert RuntimeWorker._instance.status == ServiceStatus.RUNNING
        assert (
            RuntimeWorker._instance.tracer == enable_kwargs["tracer"]
            if "tracer" in enable_kwargs
            else RuntimeWorker._instance.tracer is not None
        )
        assert (
            RuntimeWorker._instance.dogstatsd_url == enable_kwargs["dogstatsd_url"]
            if "dogstatsd_url" in enable_kwargs
            else RuntimeWorker._instance.dogstatsd_url is None
        )
        assert (
            RuntimeWorker._instance.interval == enable_kwargs["flush_interval"]
            if "flush_interval" in enable_kwargs
            else RuntimeWorker._instance.interval == 10.0
        )
    finally:
        RuntimeMetrics.disable()


@pytest.mark.parametrize(
    "environ",
    (
        dict(),
        dict(DD_RUNTIME_METRICS_INTERVAL="0.0"),
        dict(DD_RUNTIME_METRICS_INTERVAL="100.0"),
    ),
)
def test_runtime_metrics_enable_environ(monkeypatch, environ):
    try:
        for k, v in environ.items():
            monkeypatch.setenv(k, v)

        RuntimeMetrics.enable()

        assert RuntimeWorker._instance is not None
        assert RuntimeWorker._instance.status == ServiceStatus.RUNNING
        assert (
            RuntimeWorker._instance.interval == float(environ["DD_RUNTIME_METRICS_INTERVAL"])
            if "DD_RUNTIME_METRICS_INTERVAL" in environ
            else RuntimeWorker._instance.interval == 10.0
        )
    finally:
        RuntimeMetrics.disable()
