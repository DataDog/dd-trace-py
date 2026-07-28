"""
Reproducer for the TraceExporterPy::Drop GIL-hold regression (4.9 + PR #18363).

Uses a fake HTTP "agent" with a configurable response delay on the /info endpoint
to emulate a real Datadog agent under load.

The sequence in the child (without fix):
  1. NativeRuntime.after_fork_child [hook 6]:
       - Creates a new tokio runtime
       - Resets and restarts the info_fetcher worker
       - info_fetcher immediately starts GET /info → hits the fake slow agent
  2. Tracer._child_after_fork [hook 7]:
       - _recreate() drops the old NativeWriter ref
       - Old writer's refcount = 1 (cycle only, GC-collectible since PR #18363)
  3. gc.collect() in child:
       - GC detects the NativeWriter → _worker._target → NativeWriter cycle
       - tp_clear fires, NativeWriter freed, TraceExporterPy::Drop triggered
       - Drop calls shutdown_workers() → info_fetcher.stop() → pause()
       - pause() cancels the token but must await the in-flight /info request
       - BLOCKS for up to AGENT_DELAY seconds (or 3s timeout, whichever first)
       - GIL held the entire time → child frozen → missed ping deadline

With the fix (_discard_writer_exporter() called before _recreate()):
  - exporter.inner is already None when Drop fires → no-op → fast GC
"""

import http.server
import json
import os
import sys
import threading
import time

AGENT_DELAY = float(os.environ.get("AGENT_DELAY", "2.0"))   # seconds to delay /info
DEADLINE_MS = float(os.environ.get("DEADLINE_MS", "500"))   # expected max latency
NFORKS = int(os.environ.get("NFORKS", "10"))
MODE = os.environ.get("MODE", "unknown")


# ---------------------------------------------------------------------------
# Fake Datadog agent: slow /info, instant /v0.4/traces
# ---------------------------------------------------------------------------

class FakeAgentHandler(http.server.BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # silence access log

    def do_GET(self):
        if self.path == "/info":
            # Simulate a slow / overloaded agent
            time.sleep(AGENT_DELAY)
            body = json.dumps({
                "version": "7.99.0",
                "endpoints": ["/v0.4/traces"],
                "client_drop_p0s": False,
                "config": {},
                "container_tags_hash": None,
                "peer_tags": [],
                "state_hash": "abc123",
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        # Accept any trace payload immediately
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        body = b'{"rate_by_service":{}}'
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def start_fake_agent():
    server = http.server.HTTPServer(("127.0.0.1", 0), FakeAgentHandler)
    port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, port


# ---------------------------------------------------------------------------
# Main: measure fork-to-ping latency with a slow /info endpoint
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    server, port = start_fake_agent()
    agent_url = f"http://127.0.0.1:{port}"

    # Must set before importing ddtrace so the writer picks it up
    os.environ["DD_TRACE_AGENT_URL"] = agent_url
    os.environ["DD_TRACE_STARTUP_LOGS"] = "0"

    sys.path.insert(0, "/src")
    import gc
    import ddtrace
    from ddtrace.internal.writer.writer import NativeWriter

    writer = ddtrace.tracer._span_aggregator.writer
    assert isinstance(writer, NativeWriter), f"Expected NativeWriter, got {type(writer)}"

    # Hold a ref to the old exporter so we can check its state in the child
    old_exporter = writer._exporter

    print(
        f"[{MODE}] fake agent on :{port}  /info delay={AGENT_DELAY}s  "
        f"deadline={DEADLINE_MS}ms  nforks={NFORKS}",
        flush=True,
    )

    latencies = []
    exporter_states = []

    for fork_n in range(NFORKS):
        # Pre-prime the gen-0 GC threshold so any allocation in the child
        # crosses it and triggers a cycle collection.
        _junk = [object() for _ in range(700)]
        del _junk

        r_fd, w_fd = os.pipe()
        t_fork = time.monotonic()
        pid = os.fork()

        if pid == 0:
            os.close(r_fd)
            # At this point:
            #   - NativeRuntime.after_fork_child [hook 6] has run:
            #       new tokio runtime created, info_fetcher restarted,
            #       immediately starts GET /info → hits the slow fake agent
            #   - Tracer._child_after_fork [hook 7] has run:
            #       _recreate() dropped the old NativeWriter ref
            #       (with fix: _discard_writer_exporter() already took inner)

            # Trigger GC — this is the moment the 3s block fires (without fix)
            gc.collect()

            # How long did it take to get here?
            ping_ms = (time.monotonic() - t_fork) * 1000

            # Report exporter state so parent can verify fix correctness
            inner_state = b"NONE" if old_exporter.debug() == "None" else b"LIVE"
            msg = inner_state + f"|{ping_ms:.0f}".encode()
            os.write(w_fd, msg)
            os.close(w_fd)
            os._exit(0)

        os.close(w_fd)
        raw = os.read(r_fd, 32).decode()
        elapsed_ms = (time.monotonic() - t_fork) * 1000
        os.close(r_fd)
        os.waitpid(pid, 0)

        inner_state, child_gc_ms = raw.split("|")
        latencies.append(elapsed_ms)
        exporter_states.append(inner_state)

        status = "FAIL ❌" if elapsed_ms > DEADLINE_MS else "ok  ✓"
        print(
            f"  fork {fork_n:2d}: parent={elapsed_ms:7.0f}ms  "
            f"child_gc={float(child_gc_ms):7.0f}ms  "
            f"exporter_inner={inner_state}  {status}",
            flush=True,
        )

    avg = sum(latencies) / len(latencies)
    p95 = sorted(latencies)[int(len(latencies) * 0.95)]
    failures = sum(l > DEADLINE_MS for l in latencies)
    drop_called = sum(s == "NONE" for s in exporter_states)

    print(flush=True)
    print(f"  drop() called before GC: {drop_called}/{NFORKS}", flush=True)
    print(
        f"  avg={avg:.0f}ms  p95={p95:.0f}ms  "
        f"failures={failures}/{NFORKS}  deadline={DEADLINE_MS}ms",
        flush=True,
    )
    print(flush=True)

    server.shutdown()
