import sys

import flask
import random
import time
import opentelemetry

import ddtrace
from ddtrace.opentelemetry import TracerProvider
from ddtrace.opentelemetry import MeterProvider
from tests.webclient import PingFilter


opentelemetry.trace.set_tracer_provider(TracerProvider())
opentelemetry.metrics.set_meter_provider(MeterProvider())
ddtrace.tracer.configure(trace_processors=[PingFilter()])
app = flask.Flask(__name__)

otelmeter = opentelemetry.metrics.get_meter(__name__)
manual_counter = otelmeter.create_counter("otel-flask-manual-counter")
manual_updown_counter = otelmeter.create_up_down_counter("otel-flask-manual-updown-counter")
manual_gauge = otelmeter.create_gauge("otel-flask-manual-gauge")
manual_histogram = otelmeter.create_histogram("otel-flask-manual-histogram")

@app.route("/")
def index():
    return "hello"


@app.route("/otel")
def otel():
    start = time.time_ns()

    manual_counter.add(1, {"manual_api": "true"})
    manual_updown_counter.add(1, {"manual_api": "true"})
    manual_gauge.set(random.randrange(10), {"manual_api": "true"})
    manual_histogram.record(time.time_ns() - start, {"manual_api": "true"})

    oteltracer = opentelemetry.trace.get_tracer(__name__)
    with oteltracer.start_as_current_span("otel-flask-manual-span"):
        return "otel", 200


@app.route("/shutdown")
def shutdown():
    ddtrace.tracer.shutdown()
    sys.exit(0)
