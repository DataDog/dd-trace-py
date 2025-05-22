from flask import Flask

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE


app = Flask(__name__)


@app.route("/")
def index():
    return "OK", 200


@app.route("/start_application")
def starting_app_view():
    # We must call app-started before telemetry events can be sent to the agent.
    # This endpoint mocks the behavior of the agent writer.
    telemetry_writer._app_started()
    return "OK", 200


@app.route("/count_metric")
def metrics_view():
    telemetry_writer.add_count_metric(
        TELEMETRY_NAMESPACE.TRACERS,
        "test_metric",
        1.0,
    )
    return "OK", 200
