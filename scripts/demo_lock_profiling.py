#!/usr/bin/env python
# Set profiling env vars BEFORE any ddtrace imports
import os
import sys


# DD_TRACE_AGENT_URL is auto-set to localhost:8136 (common dev setup)
# No validation needed - the agent will handle the API key

os.environ["DD_PROFILING_ENABLED"] = "true"
os.environ["DD_PROFILING_LOCK_ENABLED"] = "true"
# # Capture 100% of lock events for demo purposes (default is 1%)
# os.environ.setdefault("DD_PROFILING_CAPTURE_PCT", "100")
os.environ.setdefault("DD_TRACE_AGENT_URL", "http://localhost:8136")
# Disable module unloading to ensure lock profiler patches persist
# (otherwise threading module gets unloaded and re-imported without patches)
os.environ["DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE"] = "false"

# If not run via ddtrace-run, re-exec with ddtrace-run
if "ddtrace" not in sys.modules and os.environ.get("_DD_DEMO_REEXEC") != "1":
    os.environ["_DD_DEMO_REEXEC"] = "1"
    # Try to find ddtrace-run in the same directory as python
    python_dir = os.path.dirname(sys.executable)
    ddtrace_run = os.path.join(python_dir, "ddtrace-run")
    if os.path.exists(ddtrace_run):
        os.execv(ddtrace_run, [ddtrace_run, sys.executable] + sys.argv)
    else:
        print("Warning: ddtrace-run not found. Run with: ddtrace-run python demo_lock_profiling.py")

del sys


r"""
Demo script for lock profiling with various primitives.

Usage:
  DD_SERVICE=lock-demo python demo_lock_profiling.py [PRIMITIVE]

Auto-configured:
  - DD_PROFILING_ENABLED=true
  - DD_PROFILING_LOCK_ENABLED=true
  - DD_TRACE_AGENT_URL=http://localhost:8136 (if not set)
  - DD_UNLOAD_MODULES_FROM_SITECUSTOMIZE=false (required for lock profiling)
  - Auto re-exec with ddtrace-run

Primitives:
  threading-lock              threading.Lock
  threading-rlock             threading.RLock
  threading-semaphore         threading.Semaphore (default)
  threading-bounded-semaphore threading.BoundedSemaphore
  threading-condition         threading.Condition
  asyncio-lock                asyncio.Lock
  asyncio-semaphore           asyncio.Semaphore
  asyncio-bounded-semaphore   asyncio.BoundedSemaphore
  asyncio-condition           asyncio.Condition
  all                         All primitives

Examples:
  DD_SERVICE=lock-demo python demo_lock_profiling.py
  DD_SERVICE=lock-demo python demo_lock_profiling.py all
  DD_SERVICE=lock-demo python demo_lock_profiling.py asyncio-lock

Optional Environment Variables:
  DD_SERVICE              Service name in Datadog UI (recommended)
  DD_ENV                  Environment (e.g., dev, staging, prod)
  DD_TRACE_AGENT_URL      Override agent URL (default: http://localhost:8136)
"""

import argparse  # noqa: E402
import asyncio  # noqa: E402
import random  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from typing import Callable  # noqa: E402
from typing import List  # noqa: E402
from urllib.parse import quote  # noqa: E402


# =============================================================================
# NOTE: Lock primitives are created inside each demo function (not at module level)
# to ensure profiler patches are active when the lock is created.
# =============================================================================


# =============================================================================
# THREADING DEMOS
# =============================================================================


def _run_threaded_workers(target: Callable[[int], None], num_workers: int, prefix: str) -> None:
    """Helper to run threaded workers."""
    threads: List[threading.Thread] = []
    for i in range(num_workers):
        t = threading.Thread(target=target, args=(i,), name=f"{prefix}-{i}")
        threads.append(t)
        t.start()
    for t in threads:
        t.join()


def demo_threading_lock() -> None:
    """Demo threading.Lock with contention."""
    print("\n[threading.Lock] Starting demo...")
    mutex = threading.Lock()

    def worker(worker_id: int) -> None:
        for i in range(30):
            with mutex:
                time.sleep(random.uniform(0.02, 0.08))
            if i % 10 == 0:
                print(f"  Lock worker-{worker_id}: iteration {i}")

    _run_threaded_workers(worker, num_workers=8, prefix="Lock")


def demo_threading_rlock() -> None:
    """Demo threading.RLock with contention and reentrant locking."""
    print("\n[threading.RLock] Starting demo...")
    rlock = threading.RLock()

    def worker(worker_id: int) -> None:
        for i in range(30):
            with rlock:
                with rlock:  # Reentrant - same thread can acquire again
                    time.sleep(random.uniform(0.02, 0.08))
            if i % 10 == 0:
                print(f"  RLock worker-{worker_id}: iteration {i}")

    _run_threaded_workers(worker, num_workers=8, prefix="RLock")


def demo_threading_semaphore() -> None:
    """Demo threading.Semaphore with contention."""
    print("\n[threading.Semaphore] Starting demo...")
    sema = threading.Semaphore(3)

    def worker(worker_id: int) -> None:
        for i in range(30):
            with sema:
                time.sleep(random.uniform(0.02, 0.08))
            if i % 10 == 0:
                print(f"  Semaphore worker-{worker_id}: iteration {i}")

    _run_threaded_workers(worker, num_workers=8, prefix="Semaphore")


def demo_threading_bounded_semaphore() -> None:
    """Demo threading.BoundedSemaphore with contention."""
    print("\n[threading.BoundedSemaphore] Starting demo...")
    bsema = threading.BoundedSemaphore(5)

    def worker(worker_id: int) -> None:
        for i in range(30):
            with bsema:
                time.sleep(random.uniform(0.02, 0.08))
            if i % 10 == 0:
                print(f"  BoundedSem worker-{worker_id}: iteration {i}")

    _run_threaded_workers(worker, num_workers=10, prefix="BoundedSem")


def demo_threading_condition() -> None:
    """Demo threading.Condition with contention."""
    print("\n[threading.Condition] Starting demo...")
    cond = threading.Condition()

    def worker(worker_id: int) -> None:
        for i in range(30):
            with cond:
                time.sleep(random.uniform(0.02, 0.08))
            if i % 10 == 0:
                print(f"  Condition worker-{worker_id}: iteration {i}")

    _run_threaded_workers(worker, num_workers=8, prefix="Condition")


# =============================================================================
# ASYNCIO DEMOS
# =============================================================================


def demo_asyncio_lock() -> None:
    """Demo asyncio.Lock with contention."""
    print("\n[asyncio.Lock] Starting demo...")

    async def workload() -> None:
        mutex = asyncio.Lock()

        async def worker(worker_id: int) -> None:
            for i in range(30):
                async with mutex:
                    await asyncio.sleep(random.uniform(0.02, 0.08))
                if i % 10 == 0:
                    print(f"  Async Lock worker-{worker_id}: iteration {i}")

        await asyncio.gather(*[worker(i) for i in range(8)])

    asyncio.run(workload())


def demo_asyncio_semaphore() -> None:
    """Demo asyncio.Semaphore with contention."""
    print("\n[asyncio.Semaphore] Starting demo...")

    async def workload() -> None:
        sema = asyncio.Semaphore(3)

        async def worker(worker_id: int) -> None:
            for i in range(30):
                async with sema:
                    await asyncio.sleep(random.uniform(0.02, 0.08))
                if i % 10 == 0:
                    print(f"  Async Semaphore worker-{worker_id}: iteration {i}")

        await asyncio.gather(*[worker(i) for i in range(8)])

    asyncio.run(workload())


def demo_asyncio_bounded_semaphore() -> None:
    """Demo asyncio.BoundedSemaphore with contention."""
    print("\n[asyncio.BoundedSemaphore] Starting demo...")

    async def workload() -> None:
        bsema = asyncio.BoundedSemaphore(5)

        async def worker(worker_id: int) -> None:
            for i in range(30):
                async with bsema:
                    await asyncio.sleep(random.uniform(0.02, 0.08))
                if i % 10 == 0:
                    print(f"  Async BoundedSem worker-{worker_id}: iteration {i}")

        await asyncio.gather(*[worker(i) for i in range(10)])

    asyncio.run(workload())


def demo_asyncio_condition() -> None:
    """Demo asyncio.Condition with contention."""
    print("\n[asyncio.Condition] Starting demo...")

    async def workload() -> None:
        cond = asyncio.Condition()

        async def worker(worker_id: int) -> None:
            for i in range(30):
                async with cond:
                    await asyncio.sleep(random.uniform(0.02, 0.08))
                if i % 10 == 0:
                    print(f"  Async Condition worker-{worker_id}: iteration {i}")

        await asyncio.gather(*[worker(i) for i in range(8)])

    asyncio.run(workload())


# =============================================================================
# MAIN
# =============================================================================

PRIMITIVES = {
    "threading-lock": ("threading.Lock", demo_threading_lock),
    "threading-rlock": ("threading.RLock", demo_threading_rlock),
    "threading-semaphore": ("threading.Semaphore", demo_threading_semaphore),
    "threading-bounded-semaphore": ("threading.BoundedSemaphore", demo_threading_bounded_semaphore),
    "threading-condition": ("threading.Condition", demo_threading_condition),
    "asyncio-lock": ("asyncio.Lock", demo_asyncio_lock),
    "asyncio-semaphore": ("asyncio.Semaphore", demo_asyncio_semaphore),
    "asyncio-bounded-semaphore": ("asyncio.BoundedSemaphore", demo_asyncio_bounded_semaphore),
    "asyncio-condition": ("asyncio.Condition", demo_asyncio_condition),
}


def validate_profiler_build() -> None:
    """Check if the profiler modules are properly built."""
    # Check if Cython extensions are compiled
    try:
        from ddtrace.profiling.collector import stack  # noqa: F401
    except (ModuleNotFoundError, ImportError) as e:
        print("=" * 70)
        print("PROFILER BUILD ERROR")
        print("=" * 70)
        print(f"\n❌ Profiler module import failed: {e}")
        print()
        print("To fix, run these commands:")
        print("  cd /path/to/dd-trace-py")
        print("  find ddtrace -name '*.cpython-*-darwin.so' -delete")
        print("  pip install -e . --no-build-isolation")
        print()
        raise SystemExit(1)


def validate_environment() -> None:
    """Validate optional environment variables and print warnings."""
    # DD_API_KEY is checked at script startup before ddtrace loads
    if not os.environ.get("DD_SERVICE"):
        print("⚠️  DD_SERVICE is not set. Your service will appear as 'unnamed-python-service'.")
        print("   Set it with: export DD_SERVICE=lock-demo")
        print()


def generate_profiler_url() -> str:
    """Generate a Datadog profiler URL to view lock profiling data."""
    service = os.environ.get("DD_SERVICE", "unnamed-python-service")
    env = os.environ.get("DD_ENV", "")
    site = os.environ.get("DD_SITE", "datadoghq.com")

    # Build query - use simple format without URL encoding for readability
    # Datadog UI handles unencoded queries
    query_parts = [f"service:{service}"]
    if env:
        query_parts.append(f"env:{env}")
    query = " ".join(query_parts)

    # Profiler URL format: /profiling/explorer with query filter
    url = f"https://app.{site}/profiling/explorer?query={quote(query)}"
    return url


def main() -> None:
    # Validate environment and profiler build before doing anything else
    validate_environment()
    validate_profiler_build()
    parser = argparse.ArgumentParser(
        description="Demo lock profiling with various primitives. See module docstring for full usage.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "primitive",
        nargs="?",
        default="threading-semaphore",
        choices=list(PRIMITIVES.keys()) + ["all"],
        help="Lock primitive to demo (default: threading-semaphore)",
    )
    args = parser.parse_args()

    print("=" * 70)
    print("LOCK PROFILING DEMO")
    print("=" * 70)

    if args.primitive == "all":
        demos = list(PRIMITIVES.values())
    else:
        demos = [PRIMITIVES[args.primitive]]

    print(f"\nPrimitives: {', '.join(name for name, _ in demos)}")
    print("\nExpected in Datadog UI (APM -> Profiler -> Lock Wait/Hold Time):")
    print("  - Lock names based on variable names in each demo function")
    print("  - Stack traces pointing to worker functions in this file")
    print()

    start = time.time()
    for name, demo_fn in demos:
        demo_fn()
        print(f"  ✓ {name} done")

    print(f"\nCompleted in {time.time() - start:.2f}s")
    print("Waiting 5s for profiler to flush...")
    time.sleep(5)

    print("\n" + "=" * 70)
    print("VIEW PROFILING DATA")
    print("=" * 70)
    url = generate_profiler_url()
    print("\nOpen this URL to view lock profiling data in Datadog:\n")
    print(f"  {url}")
    print()
    print("Done!")


if __name__ == "__main__":
    main()
