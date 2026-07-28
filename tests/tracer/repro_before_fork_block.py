"""
Reproducer for Path 2: NativeRuntime.before_fork() blocks os.fork() itself
when a Datadog agent is slow to respond to GET /info.

Shows per-hook timing so you can pinpoint which before_fork hook is slow.

Usage:
    AGENT_DELAY=2.0 python tests/tracer/repro_before_fork_block.py

Expected output with AGENT_DELAY=2.0:
    os.fork() blocked for: ~3000ms

    before_fork hook timing (parent):
      [0]       0ms  threads:_before_fork
      [1]    3005ms  NativeRuntime.before_fork   ← SLOW
      [2]       0ms  Tracer._sample_before_fork
"""
import http.server
import json
import os
import sys
import threading
import time

AGENT_DELAY = float(os.environ.get("AGENT_DELAY", "2.0"))
NFORKS = int(os.environ.get("NFORKS", "5"))

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
os.environ.setdefault("DD_TRACE_STARTUP_LOGS", "0")


class FakeAgent(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        if self.path == "/info":
            time.sleep(AGENT_DELAY)
        body = json.dumps({
            "version": "7.0",
            "endpoints": ["/v0.4/traces"],
            "state_hash": "x",
        }).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        self.rfile.read(length)
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")


def main():
    srv = http.server.HTTPServer(("127.0.0.1", 0), FakeAgent)
    port = srv.server_address[1]
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    os.environ["DD_TRACE_AGENT_URL"] = "http://127.0.0.1:%d" % port

    import ddtrace  # noqa: F401
    from ddtrace.internal import forksafe

    original_before = list(forksafe._registry_before_fork)
    before_times = {}

    def make_timed(i, hook):
        def timed():
            t0 = time.monotonic()
            hook()
            before_times[i] = (time.monotonic() - t0) * 1000
        return timed

    for i, hook in enumerate(original_before):
        forksafe._registry_before_fork[i] = make_timed(i, hook)

    print("Fake agent on :%d  /info delay=%.1fs  nforks=%d" % (port, AGENT_DELAY, NFORKS),
          flush=True)

    for fork_n in range(NFORKS):
        before_times.clear()
        t0 = time.monotonic()
        pid = os.fork()
        if pid == 0:
            os._exit(0)
        os.waitpid(pid, 0)
        elapsed = (time.monotonic() - t0) * 1000

        print("\nfork %d: os.fork() blocked for %.0fms" % (fork_n, elapsed), flush=True)
        print("before_fork hook timing (parent):", flush=True)
        for i, hook in enumerate(original_before):
            fn = getattr(getattr(hook, "__func__", hook), "__qualname__", repr(hook))
            ms = before_times.get(i, 0)
            marker = "  ← SLOW" if ms > 200 else ""
            print("  [%d] %7.0fms  %s%s" % (i, ms, fn, marker), flush=True)

    srv.shutdown()


if __name__ == "__main__":
    main()
