"""A traced Flask HTTP server; the driver runs in a separate process."""

import json
from pathlib import Path
import time

from flask import Flask
from flask import request
import psutil
from waitress import create_server

import ddtrace
from ddtrace import tracer
from ddtrace.appsec._processor import AppSecSpanProcessor
import ddtrace.auto  # noqa: F401
from ddtrace.internal.settings.asm import config as asm_config
from ddtrace.internal.writer import AgentWriterInterface


class TraceSink(AgentWriterInterface):
    """Count completed WAF traces without agent transport or retained span graphs."""

    _api_version = "v0.4"
    _sync_mode = True

    def __init__(self):
        self.waf_traces = 0
        self.events = 0
        self.timeouts = 0

    def write(self, spans=None):
        for span in spans or ():
            if span.get_metric("_dd.appsec.waf.duration") is not None:
                self.waf_traces += 1
                self.events += bool(span.get_tag("appsec.event"))
                self.timeouts += span.get_metric("_dd.appsec.waf.timeouts") or 0

    def set_test_session_token(self, token):
        pass

    def flush_queue(self, raise_exc=False):
        pass

    def stop(self, timeout=None):
        pass

    def recreate(self, **kwargs):
        return TraceSink()


sink = TraceSink()
tracer._span_aggregator.writer = sink
app = Flask(__name__)


@app.route("/", methods=["GET", "POST"])
def index():
    data = request.get_json(silent=True) if request.method == "POST" else None
    return {"rows": len(data.get("rows", ())) if data else 0}


process = psutil.Process()


def snapshot():
    processor = AppSecSpanProcessor._instance
    if processor is None:
        raise RuntimeError("AppSec processor was not activated")
    waf = processor._ddwaf
    return {
        "cpu_seconds": time.process_time(),
        "rss_bytes": process.memory_info().rss,
        "waf_traces": sink.waf_traces,
        "events": sink.events,
        "timeouts": sink.timeouts,
        "backend": type(waf).__module__,
        "checkout": str(Path(ddtrace.__file__).resolve().parents[1]),
        "native_version": asm_config._ddwaf_version,
        "required_addresses": len(waf.required_data),
        "initialized": waf.initialized,
    }


# Measurement endpoints bypass Flask and tracing, and run between load samples.
class MeasuredApplication:
    def __call__(self, environ, start_response):
        if environ["PATH_INFO"] == "/__stats":
            try:
                payload = json.dumps(snapshot()).encode()
            except Exception as error:
                payload = repr(error).encode()
                start_response("500 Internal Server Error", [("Content-Length", str(len(payload)))])
                return [payload]
            start_response("200 OK", [("Content-Type", "application/json"), ("Content-Length", str(len(payload)))])
            return [payload]
        return app(environ, start_response)


server = create_server(MeasuredApplication(), host="127.0.0.1", port=0, threads=1)
print(json.dumps({"port": server.effective_port}), flush=True)
server.run()
