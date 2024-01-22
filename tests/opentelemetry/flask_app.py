import os
import sys

import flask
import opentelemetry

import ddtrace
from ddtrace.opentelemetry import TracerProvider
from tests.webclient import PingFilter


# Enable ddtrace context management and set the datadog tracer provider
os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"
opentelemetry.trace.set_tracer_provider(TracerProvider())

ddtrace.tracer.configure(settings={"FILTERS": [PingFilter()]})
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
