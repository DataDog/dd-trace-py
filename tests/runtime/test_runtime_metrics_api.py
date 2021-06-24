from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
from ddtrace.internal.service import ServiceStatus
from ddtrace.runtime import RuntimeMetrics


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


def test_manually_start_runtime_metrics(run_python_code_in_subprocess):
    """
    When importing and manually starting runtime metrics
        Runtime metrics worker starts and there are no errors
    """
    out, err, status, pid = run_python_code_in_subprocess(
        """
from ddtrace.runtime import RuntimeMetrics
RuntimeMetrics.enable()

from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
assert RuntimeWorker._instance is not None

RuntimeMetrics.disable()
assert RuntimeWorker._instance is None
""",
    )

    assert status == 0


def test_start_runtime_metrics_via_env_var(ddtrace_run_python_code_in_subprocess):
    """
    When running with ddtrace-run and DD_RUNTIME_METRICS_ENABLED is set
        Runtime metrics worker starts and there are no errors
    """

    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
assert RuntimeWorker._instance is None
"""
    )
    assert status == 0

    _, _, status, _ = ddtrace_run_python_code_in_subprocess(
        """
from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
assert RuntimeWorker._instance is not None
""",
        env={"DD_RUNTIME_METRICS_ENABLED": "true"},
    )
    assert status == 0
