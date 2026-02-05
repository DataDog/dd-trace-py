"""
Benchmark fork time overhead with ddtrace.

Measures the time to fork a process when ddtrace is imported and configured
in the parent process (simulating gunicorn preload mode with a real Flask app).
"""

import multiprocessing
import os
import time

import bm


def _child_process(conn, parent_fork_time):
    """Child process

    Measures fork overhead by recording when the child starts executing
    and comparing to when the parent initiated the fork.
    """
    fork_overhead = time.perf_counter() - parent_fork_time
    conn.send(fork_overhead)
    conn.close()


class ForkTime(bm.Scenario):
    """Measure fork overhead with ddtrace."""

    configure: bool

    cprofile_loops: int = 0  # Fork benchmarks don't work well with cprofile

    def _pyperf(self, loops: int) -> float:
        return self.run_fork(loops)

    def run_fork(self, loops: int) -> float:
        if self.configure:
            os.environ["DD_TRACE_ENABLED"] = "true"
            os.environ["DD_SERVICE"] = "fork-benchmark"
            os.environ["DD_ENV"] = "benchmark"

            from ddtrace import tracer

            tracer.configure()

            try:
                from flask import Flask

                app = Flask(__name__)  # noqa: F841

                @app.route("/")
                def hello():
                    return "Hello"

                try:
                    import requests  # noqa: F401
                except ImportError:
                    pass

                try:
                    import pymongo  # noqa: F401
                except ImportError:
                    pass

            except ImportError:
                pass

        # Yield fork overhead for each iteration
        total = 0.0
        for _ in range(loops):
            parent_conn, child_conn = multiprocessing.Pipe()
            fork_start = time.perf_counter()
            p = multiprocessing.Process(target=_child_process, args=(child_conn, fork_start))
            p.start()
            fork_overhead = parent_conn.recv()
            p.join()
            parent_conn.close()
            total += fork_overhead
        return total
