import sys

import flask
import opentelemetry

import ddtrace
from ddtrace.opentelemetry import TracerProvider
from tests.webclient import PingFilter


opentelemetry.trace.set_tracer_provider(TracerProvider())

ddtrace.tracer.configure(trace_processors=[PingFilter()])
app = flask.Flask(__name__)


@app.route("/")
def index():
    return "hello"


@app.route("/otel")
def otel():
    oteltracer = opentelemetry.trace.get_tracer(__name__)
    with oteltracer.start_as_current_span("otel-flask-manual-span"):
        return "otel", 200


@app.route("/shutdown")
def shutdown():
    ddtrace.tracer.shutdown()
    sys.exit(0)
