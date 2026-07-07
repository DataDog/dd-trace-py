#!/usr/bin/env python3
"""
Stress-test ddtrace's fork hooks while PeriodicThread instances are active.

Unlike repro_fork_deadlock_logging.py, this does not install a deliberately
non-fork-safe logging handler. It is intended to look for native post-fork
restart deadlocks/timeouts under repeated fork activity.
"""

import logging
import multiprocessing
import os
import signal
import sys
import threading
import time
import typing as t


logging.basicConfig(level=logging.CRITICAL)
logging.getLogger("ddtrace").disabled = True

import ddtrace  # noqa: E402,F401
from ddtrace.trace import tracer  # noqa: E402
from ddtrace.version import __version__  # noqa: E402


def safely_fork(timeout_s: float = 0.5) -> int:
    recv, send = multiprocessing.Pipe(False)
    child_pid = os.fork()

    if child_pid > 0:
        send.close()
        if recv.poll(timeout_s):
            pid = t.cast(int, recv.recv())
            os.waitpid(child_pid, 0)
            return pid

        os.kill(child_pid, signal.SIGKILL)
        os.waitpid(child_pid, 0)
        raise TimeoutError(f"child {child_pid} did not reach user code after fork")

    recv.close()
    send.send(os.getpid())
    os._exit(0)


def main() -> int:
    print(f"ddtrace {__version__}, python {sys.version.split()[0]}, pid {os.getpid()}", flush=True)

    stop = threading.Event()

    def trace_generator() -> None:
        while not stop.is_set():
            with tracer.trace("fork.stress", service="fork-stress"):
                time.sleep(0.001)

    for _ in range(4):
        threading.Thread(target=trace_generator, daemon=True).start()

    time.sleep(1)

    successes = 0
    failures = 0
    iterations = int(os.environ.get("REPRO_FORK_ITERATIONS", "200"))
    timeout_s = float(os.environ.get("REPRO_FORK_TIMEOUT", "0.5"))

    for i in range(iterations):
        try:
            safely_fork(timeout_s=timeout_s)
            successes += 1
        except TimeoutError as e:
            failures += 1
            print(f"[TIMEOUT] iteration={i}: {e}", flush=True)
        time.sleep(0.005)

    stop.set()
    print(f"=== {successes} OK, {failures} timeouts ===", flush=True)
    return 124 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
