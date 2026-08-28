import os

from flask import Flask
from flask import jsonify
from opentelemetry import metrics
from opentelemetry import trace

from ddtrace import config


app = Flask(__name__, static_folder=None)
otel_tracer = trace.get_tracer(__name__)
request_counter = metrics.get_meter(__name__).create_counter(
    "moontest.agentless.requests",
    unit="{request}",
    description="Requests handled by the agentless Remote Configuration sample",
)


@app.get("/")
def index():
    with otel_tracer.start_as_current_span("moontest.agentless.index") as span:
        span.set_attribute("http.route", "/")
        request_counter.add(1, {"http.route": "/"})
        return jsonify(
            env=config.env,
            metric="moontest.agentless.requests",
            service=config.service,
            span="moontest.agentless.index",
        )


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "8051")))
