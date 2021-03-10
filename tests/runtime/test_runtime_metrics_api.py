from ddtrace.internal.runtime.runtime_metrics import RuntimeWorker
from ddtrace.runtime import RuntimeMetrics


def test_runtime_metrics_api():
    RuntimeMetrics.enable()
    assert RuntimeWorker._instance is not None
    assert RuntimeWorker._instance.is_alive()

    RuntimeMetrics.disable()
    assert RuntimeWorker._instance is None
