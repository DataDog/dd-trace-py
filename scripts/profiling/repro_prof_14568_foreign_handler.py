#!/usr/bin/env python3
"""Repro for the stack-profiler SIGSEGV under a foreign SIGSEGV handler (PROF-14568, Layer 1).

``safe_memcpy``'s fault recovery only works while the profiler owns the
``SIGSEGV`` disposition. Libraries such as PyTorch/CUDA install their own
``SIGSEGV`` handler after the profiler has started. Once that happens, a fault on
a stale read (which the profiler would normally recover from via ``siglongjmp``)
is delivered to the foreign handler instead, and the process crashes.

This script reproduces the condition:

  1. Start the profiler with fast copy enabled.
  2. Overwrite the ``SIGSEGV`` disposition with a foreign handler (here, the
     default disposition, standing in for torch/CUDA installing their own).
  3. Churn deep Python stacks while the sampler runs.

On ``main`` the sampler keeps using ``safe_memcpy`` even though it no longer owns
the handler, so a fault terminates the process. With the fix, the sampler detects
within ~2s that it lost handler ownership and falls back to ``process_vm_readv``
(Linux) / ``mach_vm_read_overwrite`` (macOS), so the process survives.

Usage:
    DD_PROFILING_STACK_FAST_COPY=1 python scripts/profiling/repro_prof_14568_foreign_handler.py

Optionally import torch to install a *real* foreign handler instead of the
synthetic one:
    DD_PROFILING_STACK_FAST_COPY=1 python scripts/profiling/repro_prof_14568_foreign_handler.py --torch

Exit code 0 means the process survived (fix working). A crash (negative exit /
SIGSEGV from the parent's perspective) reproduces the bug.
"""

from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
import time


def _busy_worker(stop_evt: threading.Event) -> None:
    def recurse(depth: int) -> int:
        if depth <= 0:
            return sum(len(str(i)) for i in range(16))
        return recurse(depth - 1)

    while not stop_evt.is_set():
        recurse(28)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--run", type=float, default=6.0, help="seconds to sample under the foreign handler")
    parser.add_argument("--torch", action="store_true", help="import torch to install a real foreign handler")
    args = parser.parse_args()

    os.environ.setdefault("DD_PROFILING_STACK_V2_ENABLED", "true")
    os.environ.setdefault("DD_PROFILING_STACK_FAST_COPY", "1")

    from ddtrace.profiling.profiler import Profiler

    prof = Profiler()
    prof.start()

    if args.torch:
        try:
            import torch  # noqa: F401

            print("torch imported; its SIGSEGV handler (if any) is now installed")
        except Exception as exc:  # pragma: no cover - torch is optional
            print(f"could not import torch ({exc}); installing synthetic foreign handler instead")
            signal.signal(signal.SIGSEGV, signal.SIG_DFL)
    else:
        # Stand in for a library that installs its own SIGSEGV handler after start.
        signal.signal(signal.SIGSEGV, signal.SIG_DFL)
        print("installed synthetic foreign SIGSEGV handler (SIG_DFL)")

    stop_evt = threading.Event()
    workers = [threading.Thread(target=_busy_worker, args=(stop_evt,), daemon=True) for _ in range(args.threads)]
    for w in workers:
        w.start()

    # Sample for longer than the handler-ownership check cadence (~2s) so the
    # fixed sampler observes the foreign handler and falls back.
    print(f"sampling for {args.run}s under a foreign SIGSEGV handler...")
    time.sleep(args.run)

    stop_evt.set()
    for w in workers:
        w.join(timeout=1.0)
    prof.stop(flush=True)

    print("OK: process survived under a foreign SIGSEGV handler")
    return 0


if __name__ == "__main__":
    sys.exit(main())
