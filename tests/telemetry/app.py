from flask import Flask

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE_TAG_TRACER


app = Flask(__name__)


@app.route("/")
def index():
    return "OK", 200


@app.route("/count_metric")
def metrics_view():
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE_TAG_TRACER,
        "test_metric",
        1.0,
    )
    return "OK", 200
