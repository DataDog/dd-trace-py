from collections.abc import Callable
from collections.abc import Generator
from http.server import BaseHTTPRequestHandler
from http.server import HTTPServer
import os
import threading
from typing import Any


# The buffer reports trace_api.* health metrics through the telemetry worker when telemetry is on,
# and enabling it would add agent I/O to the benchmark process. Nothing here needs it.
os.environ["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "false"

import bm

from ddtrace._trace.span import Span
from ddtrace.internal.writer import NativeTraceBuffer


class _OkHandler(BaseHTTPRequestHandler):
    """Acknowledge every export so libdatadog's worker never retries or queues a retry."""

    def _ok(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length:
            self.rfile.read(length)
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def do_PUT(self) -> None:
        self._ok()

    def do_POST(self) -> None:
        self._ok()

    def log_message(self, msg_format: str, *args: Any) -> None:
        # The worker exports from its own thread; request logging on stderr would add noise.
        pass


class TraceBuffer(bm.Scenario):  # type: ignore[misc]
    nspans: int
    nmetastructs: int
    lmetastructs: int
    init: bool

    def _trace(self) -> list[Span]:
        struct_value = {
            "item-%d" % i: {"v": "v" * self.lmetastructs, "seq": ["a", str(i)]} for i in range(self.nmetastructs)
        }
        trace: list[Span] = []
        for i in range(self.nspans):
            span = Span(
                "trace-buffer.span.%d" % i,
                service="bench",
                resource="GET /resource/%d" % i,
                span_type="web",
                parent_id=trace[0].span_id if trace else None,
            )
            span._set_attribute("metric.0", float(i))
            if self.nmetastructs:
                span._set_struct_tag("meta_struct", struct_value)
            span.finish()
            trace.append(span)
        return trace

    def run(self) -> Generator[Callable[[int], None], None, None]:
        if self.init:
            # The eager _msgpack.packb import runs in the constructor; everything else here
            # (exporter setup, libdatadog worker spawn, shutdown) is identical on both sides of
            # a comparison, so the delta isolates construction cost.
            def _(loops: int) -> None:
                for _ in range(loops):
                    writer = NativeTraceBuffer("http://127.0.0.1:9")
                    writer.stop(3.0)

            yield _
            return

        # A local sink keeps the write path realistic — the buffer really exports and frees its
        # bytes — without depending on an external agent.
        server = HTTPServer(("127.0.0.1", 0), _OkHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        writer = NativeTraceBuffer("http://127.0.0.1:%d" % server.server_address[1])
        try:
            trace = self._trace()

            def _(loops: int) -> None:
                for _ in range(loops):
                    writer.write(trace)

            yield _
        finally:
            writer.stop(3.0)
            server.shutdown()
            thread.join()
