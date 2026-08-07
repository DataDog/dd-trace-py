"""
Parent/worker script for subprocess profiling bootstrap regression tests.

Simulates launchers like torchrun: a ddtrace-run parent starts workers with
sys.executable (not ddtrace-run). Workers rely on inherited environment
bootstrap for profiling.
"""

from __future__ import annotations

import os
import subprocess
import sys


def _worker() -> None:
    import ddtrace.profiling.bootstrap

    profiler = ddtrace.profiling.bootstrap.profiler  # type: ignore[attr-defined]
    total = 0
    for i in range(5_000_000):
        total += i
    profiler.stop()
    print(os.getpid())


def _run_workers() -> None:
    worker_cmd = [sys.executable, __file__, "worker"]

    run_result = subprocess.run(
        worker_cmd,
        capture_output=True,
        text=True,
        check=False,
    )
    assert run_result.returncode == 0, (run_result.stdout, run_result.stderr)
    run_pid = run_result.stdout.strip()
    assert run_pid.isdigit(), run_result.stdout

    popen = subprocess.Popen(
        worker_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    popen_stdout, popen_stderr = popen.communicate(timeout=120)
    assert popen.returncode == 0, (popen_stdout, popen_stderr)
    popen_pid = popen_stdout.strip()
    assert popen_pid.isdigit(), popen_stdout

    print(run_pid)
    print(popen_pid)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "worker":
        _worker()
    else:
        _run_workers()
