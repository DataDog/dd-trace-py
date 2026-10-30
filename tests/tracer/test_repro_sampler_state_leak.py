from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import json
import os
import threading

import ddtrace
from ddtrace._trace.sampler import DatadogSampler
from ddtrace.internal.writer.writer import NativeWriter


AGENT_RATE = float(os.environ.get("REPRO_AGENT_RATE", "0.0001"))


class _FakeAgentHandler(BaseHTTPRequestHandler):
    def _respond(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        body = json.dumps({"rate_by_service": {"service:,env:": AGENT_RATE}}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    do_PUT = _respond
    do_POST = _respond

    def do_GET(self):
        body = json.dumps({"endpoints": ["/v0.4/traces", "/v0.5/traces"]}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def test_global_tracer_real_writer_applies_agent_rates():
    server = HTTPServer(("127.0.0.1", 0), _FakeAgentHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    aggregator = ddtrace.tracer._span_aggregator
    original_writer = aggregator.writer
    try:
        aggregator.writer = NativeWriter(
            intake_url="http://127.0.0.1:%d" % server.server_address[1],
            response_callback=aggregator._agent_response_callback,
        )
        with ddtrace.tracer.trace("leak.real_writer"):
            pass
        aggregator.writer.flush_queue(raise_exc=True)
        aggregator.writer.stop()
    finally:
        aggregator.writer = original_writer
        server.shutdown()

    sampler = ddtrace.tracer._sampler
    assert isinstance(sampler, DatadogSampler)
    assert {k: v.sample_rate for k, v in sampler._agent_based_samplers.items()} == {"service:,env:": AGENT_RATE}
