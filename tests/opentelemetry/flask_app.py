import sys

import flask
import opentelemetry

import ddtrace
from ddtrace.opentelemetry import TracerProvider
from tests.webclient import PingFilter


opentelemetry.trace.set_tracer_provider(TracerProvider())

ddtrace.tracer.configure(settings={"FILTERS": [PingFilter()]})
app = flask.Flask(__name__)


@app.route("/")
def index():
    return "hello"


@app.route("/otel")
def otel():
    with ddtrace.tracer.trace(name="internal", resource="otel-flask-manual-span"):
        pass
    return "otel", 200


@app.route("/shutdown")
def shutdown():
    ddtrace.tracer.shutdown()
    sys.exit(0)
