import os


# Ensure low overhead mode is disabled for sidecar.
assert os.environ.get("DD_SIDECAR_ENABLED", "").lower() in ("0", "", "false")

from http.server import BaseHTTPRequestHandler  # noqa: E402
from itertools import chain  # noqa: E402
import json  # noqa: E402
import socketserver  # noqa: E402
from typing import Any  # noqa: E402
from typing import Dict  # noqa: E402
from urllib.parse import urlparse  # noqa: E402

import ddtrace  # noqa: E402
from ddtrace._trace.processor import SpanProcessor  # noqa: E402


# Initialize global variables
spans: Dict[int, ddtrace.Span] = {}
from ddtrace.ddsidecar_utils import SIDECAR_HOST  # noqa: E402


# Create the HTTP request handler class
class SideCarHTTPRequestHandler(BaseHTTPRequestHandler):
    def _send_response(self, code: int, data: Any):
        """Helper to send JSON response"""
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def do_POST(self):
        """Handle POST requests"""
        content_length = int(self.headers["Content-Length"])
        body = self.rfile.read(content_length)
        # print("reading body", body)
        try:
            data = json.loads(body)
        except json.JSONDecodeError:
            self.send_response(400)
            self.end_headers()
            return

        parsed_path = urlparse(self.path)
        path = parsed_path.path

        # print(path)

        if path == "/trace/span/start":
            self.trace_span_start(data)
        elif path == "/trace/span/finish":
            self.trace_span_finish(data)
        elif path == "/trace/span/set_meta":
            self.trace_span_set_meta(data)
        elif path == "/trace/span/set_metric":
            self.trace_span_set_metric(data)
        elif path == "/trace/span/set_resource":
            self.trace_span_set_resource(data)
        elif path == "/trace/span/set_service":
            self.trace_span_set_service(data)
        elif path == "/trace/span/set_error":
            self.trace_span_set_error(data)
        elif path == "/trace/span/set_type":
            self.trace_span_set_type(data)
        elif path == "/trace/span/set_start_ns":
            self.trace_span_set_start_ns(data)
        elif path == "/trace/span/set_duration_ns":
            self.trace_span_set_duration_ns(data)
        elif path == "/trace/span/set_event":
            self.trace_span_set_event(data)
        elif path == "/trace/span/set_link":
            self.trace_span_set_link(data)
        elif path == "/trace/span/flush":
            self.trace_spans_flush(data)
        # elif path == "/trace/delete":
        #     self.trace_delete(data)
        elif path == "/alive":
            self._send_response(200, {})
        else:
            self.send_response(404)
            self.end_headers()

    def trace_span_start(self, args: dict):
        """Start a new span"""
        parent_id = args.get("parent_id", -1)
        if parent_id in spans:
            context = spans[parent_id].context
        else:
            context = None

        span = ddtrace.Span(
            args["name"],
            trace_id=args["trace_id"],
            parent_id=args["parent_id"],
            span_id=args["span_id"],
            service=args.get("service"),
            span_type=args.get("type"),
            resource=args.get("resource"),
            span_api=args.get("span_api", "datadog"),
            start=args.get("start"),
            context=context,
            on_finish=[ddtrace.tracer._on_span_finish],
        )

        if ddtrace.tracer.enabled or ddtrace.tracer._apm_opt_out:
            for p in chain(
                ddtrace.tracer._span_processors, SpanProcessor.__processors__, ddtrace.tracer._deferred_processors
            ):
                p.on_span_start(span)
        ddtrace.tracer._hooks.emit(ddtrace.tracer.__class__.start_span, span)

        spans[span.span_id] = span

        self._send_response(200, {"span_id": span.span_id, "trace_id": span.trace_id})

    def trace_span_finish(self, args: dict):
        """Finish an existing span"""
        global spans
        span = spans.get(args["span_id"])
        if span:
            span.finish(args.get("finish_time"))
            # print("finished span", span._pprint())
            spans.pop(args["span_id"], None)
        self._send_response(200, {})

    def trace_span_set_meta(self, args: dict):
        """Set metadata on a span"""
        span = spans.get(args["span_id"])
        if span:
            span.set_tag(args["key"], args["value"])
        self._send_response(200, {})

    def trace_span_set_metric(self, args: dict):
        """Set metric on a span"""
        span = spans.get(args["span_id"])
        if span:
            span.set_metric(args["key"], args["value"])
        self._send_response(200, {})

    def trace_span_set_resource(self, args: dict):
        """Set resource for a span"""
        span = spans.get(args["span_id"])
        if span:
            span.resource = args["resource"]
        self._send_response(200, {})

    def trace_span_set_service(self, args: dict):
        """Set service for a span"""
        span = spans.get(args["span_id"])
        if span:
            span.service = args["service"]
        self._send_response(200, {})

    def trace_span_set_error(self, args: dict):
        """Set error flag on the span"""
        span = spans.get(args["span_id"])
        if span:
            span.error = int(args["error"])
        self._send_response(200, {})

    def trace_span_set_start_ns(self, args: dict):
        """Set the start_ns (start timestamp in nanoseconds) of the span"""
        span = spans.get(args["span_id"])
        if span:
            span.start_ns = float(args["start_ns"])
        self._send_response(200, {})

    def trace_span_set_type(self, args: dict):
        """Set the type of the span"""
        span = spans.get(args["span_id"])
        if span:
            span.span_type = args["type"]
        self._send_response(200, {})

    def trace_span_set_duration_ns(self, args: dict):
        """Set the duration_ns (duration in nanoseconds) of the span"""
        span = spans.get(args["span_id"])
        if span:
            span.duration_ns = args["duration_ns"]
        self._send_response(200, {})

    def trace_span_set_event(self, args: dict):
        """Set event on the span"""
        span = spans.get(args["span_id"])
        if span:
            name = args["name"]
            attributes = json.loads(args.get("attributes") or "{}")
            timestamp = args.get("timestamp")
            span._add_event(name, attributes, timestamp)
        self._send_response(200, {})

    def trace_span_set_link(self, args: dict):
        """Set link on the span"""
        span = spans.get(args["span_id"])
        if span:
            span_id = int(args["linked_span_id"])
            trace_id = int(args["linked_trace_id"])
            attributes = json.loads(args.get("attributes") or "{}")
            tracestate = args.get("tracestate")
            flags = int(args.get("flags", 0)) or None
            span.set_link(trace_id, span_id, attributes, tracestate, flags)
            # print(span._links)
        self._send_response(200, {})

    def trace_spans_flush(self, args: dict):
        """Flush all spans"""
        ddtrace.tracer.flush()
        self._send_response(200, {})


if __name__ == "__main__":
    port = int(ddtrace.config._trace_sidecar_port)
    with socketserver.TCPServer((SIDECAR_HOST, port), SideCarHTTPRequestHandler) as httpd:
        print("Serving at port", port)
        httpd.serve_forever()
