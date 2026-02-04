"""
Benchmark fork time overhead with ddtrace.

Measures the time to fork a process when ddtrace is imported and configured
in the parent process (simulating gunicorn preload mode with a real Flask app).
"""

import multiprocessing
import os
import time

import bm


def _child_process(conn):
    """Child process - must be module-level for pickling."""
    start = time.perf_counter()
    _ = sum(i * i for i in range(1000))
    elapsed = time.perf_counter() - start
    conn.send(elapsed)
    conn.close()


class ForkTime(bm.Scenario):
    """Measure fork overhead with ddtrace in a realistic gunicorn scenario."""

    configure: bool

    cprofile_loops: int = 0  # Fork benchmarks don't work well with cprofile

    def run(self):
        # Import and optionally configure ddtrace before forking (like gunicorn preload)
        if self.configure:
            # Set environment variables that would be set in production
            os.environ["DD_TRACE_ENABLED"] = "true"
            os.environ["DD_SERVICE"] = "fork-benchmark"
            os.environ["DD_ENV"] = "benchmark"

            # Import ddtrace first (happens early in gunicorn preload)
            from ddtrace import tracer

            tracer.configure()

            # Import Flask and create app (like a real gunicorn app)
            try:
                from flask import Flask

                app = Flask(__name__)

                # Add a simple route (auto-instruments Flask)
                @app.route("/")
                def hello():
                    return "Hello"

                # Import other common libraries that get auto-instrumented
                try:
                    import requests  # noqa: F401 - imported for auto-instrumentation side effects
                except ImportError:
                    pass

                try:
                    import pymongo  # noqa: F401 - imported for auto-instrumentation side effects
                except ImportError:
                    pass

            except ImportError:
                # Flask not available, skip app creation
                pass

        def _(loops):
            for _ in range(loops):
                parent_conn, child_conn = multiprocessing.Pipe()
                p = multiprocessing.Process(target=_child_process, args=(child_conn,))
                p.start()
                parent_conn.recv()
                p.join()
                parent_conn.close()

        yield _
