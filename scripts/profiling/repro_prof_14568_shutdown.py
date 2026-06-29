#!/usr/bin/env python3
"""Repro for the stack-profiler shutdown SIGSEGV (PROF-14568 / AIPTS-1715).

The crash has two layers (see the #17929 post-mortem):

  Layer 2 (shutdown race): ``Profiler._stop_service`` runs the final
  ``scheduler.flush()`` *before* it stops the collectors. The first flush
  resolves the code-provenance file, which triggers one-time
  ``importlib.metadata`` cold imports (setuptools / _distutils_hack /
  packaging). Those imports run while the native stack sampler thread is still
  walking live frames during interpreter teardown, so the sampler can read a
  freed ``PyCodeObject*`` and fault inside ``safe_memcpy``.

  Layer 1 (handler ownership): ``safe_memcpy``'s fault recovery only works
  while we own the ``SIGSEGV`` handler. Libraries such as PyTorch/CUDA install
  their own handler, so the recovery ``siglongjmp`` never runs and the process
  crashes.

This script drives Layer 2 deterministically: it repeatedly starts and stops
the profiler with ``fast_copy`` enabled while busy worker threads churn frames,
clearing the code-provenance cache between iterations so the final flush of each
cycle performs the cold imports. The ``--torch`` flag imports torch first to
also exercise Layer 1 in a torch-like environment.

Usage:
    _DD_PROFILING_STACK_FAST_COPY=1 python scripts/profiling/repro_prof_14568_shutdown.py
    _DD_PROFILING_STACK_FAST_COPY=1 python scripts/profiling/repro_prof_14568_shutdown.py --torch

Exit code 0 means no crash was observed across all iterations; a non-zero exit
(typically -11 / SIGSEGV from the parent's perspective) reproduces the bug.
"""

from __future__ import annotations

import argparse
import os
import sys
import threading
import time


def _busy_worker(stop_evt: threading.Event) -> None:
    """Churn Python frames so the sampler always has deep stacks to unwind."""

    def recurse(depth: int) -> int:
        if depth <= 0:
            # Touch some attribute lookups / string ops so code objects stay hot.
            return sum(len(str(i)) for i in range(16))
        return recurse(depth - 1)

    while not stop_evt.is_set():
        recurse(24)


def _reset_code_provenance_cache() -> None:
    """Force the next flush to recompute provenance (re-triggering cold imports)."""
    try:
        from ddtrace.internal.datadog.profiling import code_provenance

        code_provenance._code_provenance_file_path = None
    except Exception:  # nosec: B110
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--iterations", type=int, default=50)
    parser.add_argument("--threads", type=int, default=8)
    parser.add_argument("--hold", type=float, default=0.3, help="seconds the profiler runs each cycle")
    parser.add_argument("--torch", action="store_true", help="import torch first (Layer 1)")
    args = parser.parse_args()

    # Make sure code provenance is on so the flush resolves the provenance file.
    os.environ.setdefault("DD_PROFILING_ENABLE_CODE_PROVENANCE", "true")
    os.environ.setdefault("DD_PROFILING_STACK_V2_ENABLED", "true")

    if args.torch:
        try:
            import torch  # noqa: F401

            print("torch imported; its SIGSEGV handler (if any) is now installed")
        except Exception as exc:  # pragma: no cover - torch is optional
            print(f"could not import torch ({exc}); continuing without Layer 1")

    from ddtrace.profiling.profiler import Profiler

    stop_evt = threading.Event()
    workers = [threading.Thread(target=_busy_worker, args=(stop_evt,), daemon=True) for _ in range(args.threads)]
    for w in workers:
        w.start()

    try:
        for i in range(args.iterations):
            _reset_code_provenance_cache()
            prof = Profiler()
            prof.start()
            time.sleep(args.hold)
            # stop() runs the final flush (cold imports) while the sampler is live.
            prof.stop(flush=True)
            if (i + 1) % 10 == 0:
                print(f"survived {i + 1}/{args.iterations} start/stop cycles")
    finally:
        stop_evt.set()
        for w in workers:
            w.join(timeout=1.0)

    print("OK: no crash across all start/stop cycles")
    return 0


if __name__ == "__main__":
    sys.exit(main())
