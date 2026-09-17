from flask import Flask

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


app = Flask(__name__)


@app.route("/")
def index():
    return "OK", 200


@app.route("/start_application")
def starting_app_view():
    # The native telemetry worker emits the app-started event automatically when the
    # writer is enabled, so this endpoint no longer needs to trigger it explicitly.
    # Kept as a no-op route so existing test flows that hit it continue to work.
    return "OK", 200


@app.route("/count_metric")
def metrics_view():
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "test_metric",
        1.0,
    )
    return "OK", 200
