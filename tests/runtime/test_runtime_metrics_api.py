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

from ddtrace.runtime import RuntimeMetrics

assert not RuntimeMetrics._enabled
RuntimeMetrics.enable()
assert RuntimeMetrics._enabled
telemetry_writer.periodic(force_flush=True)
    """

    _, stderr, status, _ = run_python_code_in_subprocess(code)
    assert status == 0, stderr

    runtimemetrics_enabled = test_agent_session.get_configurations("DD_RUNTIME_METRICS_ENABLED")
    assert len(runtimemetrics_enabled) == 1
    assert runtimemetrics_enabled[0]["value"]
    assert runtimemetrics_enabled[0]["origin"] == "code"


def test_manually_stop_runtime_metrics_telemetry(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """
    When importing and manually stopping runtime metrics
        Runtime metrics worker stops and we report that it is enabled via telemetry
    """
    code = """
from ddtrace.internal.telemetry import telemetry_writer

from ddtrace.runtime import RuntimeMetrics

assert RuntimeMetrics._enabled
RuntimeMetrics.disable()
assert not RuntimeMetrics._enabled
telemetry_writer.periodic(force_flush=True)
    """

    env = os.environ.copy()
    env["DD_RUNTIME_METRICS_ENABLED"] = "true"
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    runtimemetrics_enabled = test_agent_session.get_configurations("DD_RUNTIME_METRICS_ENABLED")
    assert len(runtimemetrics_enabled) == 1
    assert runtimemetrics_enabled[0]["value"] is False
    assert runtimemetrics_enabled[0]["origin"] == "code"


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


@pytest.mark.subprocess(parametrize={"DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED": ["true", "false"]})
def test_runtime_metrics_experimental_runtime_tag(DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED):
    """
    When runtime metrics is enabled and DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED=DD_RUNTIME_METRICS_ENABLED
        Runtime metrics worker starts and submits gauge metrics instead of distribution metrics
    """
    import os

    from ddtrace.internal.runtime import get_runtime_id
    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
    from ddtrace.internal.service import ServiceStatus

    RuntimeWorker.enable()
    assert RuntimeWorker._instance is not None

    worker_instance = RuntimeWorker._instance
    assert worker_instance.status == ServiceStatus.RUNNING

    runtime_id_tag = f"runtime-id:{get_runtime_id()}"
    if os.environ["DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED"] == "true":
        assert runtime_id_tag in worker_instance._platform_tags, worker_instance._platform_tags
    elif os.environ["DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED"] == "false":
        assert runtime_id_tag not in worker_instance._platform_tags, worker_instance._platform_tags
    else:
        raise pytest.fail("Invalid value for DD_TRACE_EXPERIMENTAL_RUNTIME_ID_ENABLED")


@pytest.mark.subprocess(
    parametrize={"DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED": ["DD_RUNTIME_METRICS_ENABLED,someotherfeature", ""]},
    err=None,
)
def test_runtime_metrics_experimental_metric_type(DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED):
    """
    When runtime metrics is enabled and DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED=DD_RUNTIME_METRICS_ENABLED
        Runtime metrics worker starts and submits gauge metrics instead of distribution metrics
    """
    import os

    from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
    from ddtrace.internal.service import ServiceStatus

    RuntimeWorker.enable()
    assert RuntimeWorker._instance is not None

    worker_instance = RuntimeWorker._instance
    assert worker_instance.status == ServiceStatus.RUNNING
    if "DD_RUNTIME_METRICS_ENABLED" in os.environ["DD_TRACE_EXPERIMENTAL_FEATURES_ENABLED"]:
        assert worker_instance.send_metric == worker_instance._dogstatsd_client.gauge, worker_instance.send_metric
    else:
        assert (
            worker_instance.send_metric == worker_instance._dogstatsd_client.distribution
        ), worker_instance.send_metric
