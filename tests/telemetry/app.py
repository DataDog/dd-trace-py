from flask import Flask

from ddtrace.internal.service import ServiceStatus
from ddtrace.internal.telemetry import telemetry_metrics_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_TRACER


app = Flask(__name__)


@app.route("/")
def index():
    return "OK", 200


@app.route("/metrics")
def metrics_view():
    telemetry_metrics_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_TRACER,
        "test_metric",
        1.0,
    )
    namespace_metrics = telemetry_metrics_writer._namespace.get()
    metrics = [
        m.to_dict()
        for payload_type, namespaces in namespace_metrics.items()
        for namespace, metrics in namespaces.items()
        for m in metrics.values()
    ]
    return {
        "telemetry_metrics_writer_running": telemetry_metrics_writer.status == ServiceStatus.RUNNING,
        "telemetry_metrics_writer_worker": telemetry_metrics_writer._worker is not None,
        "telemetry_metrics_writer_queue": metrics,
    }, 200
