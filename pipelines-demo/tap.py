"""Local agent stand-in for the pipelines demo.

A tiny stdlib HTTP server on :8126 that accepts POST /v0.1/pipeline_stats and
writes the RAW body bytes (the msgpack+gzip DSM payload the tracer would send
to a real agent) to pipelines-demo/tap/<unix_ts_ns>.bin, one file per payload.
Everything else gets a bare 200 so tracer writers never retry.

The dd-source dsm-auto-pipelines harness reads the tap files back and feeds
them through the same ingest path the Kafka consumer uses.
"""

import os
import time
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer


TAP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tap")
PORT = int(os.environ.get("TAP_PORT", "8126"))


class TapHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        if self.path == "/v0.1/pipeline_stats":
            os.makedirs(TAP_DIR, exist_ok=True)
            # time.time_ns(): unix timestamp, nanosecond precision so concurrent
            # payloads never share a file name.
            with open(os.path.join(TAP_DIR, "%d.bin" % time.time_ns()), "wb") as f:
                f.write(body)
        self._respond()

    def do_GET(self):
        self._respond()

    def _respond(self):
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, fmt, *args):
        print("tap: %s" % (fmt % args))


if __name__ == "__main__":
    print("tap: listening on :%d, payloads -> %s" % (PORT, TAP_DIR))
    HTTPServer(("0.0.0.0", PORT), TapHandler).serve_forever()
