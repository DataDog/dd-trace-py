"""Workload for manual tests of the profiler_config.json watcher.

Run it from the directory that holds profiler_config.json, e.g.:

    DD_PROFILING_ENABLED=1 ddtrace-run python profile_target.py

Then edit profiler_config.json while it runs. Every second the script prints the state
of the stack and memory collectors.
"""

import threading
import time
from typing import Any


def cpu_work(n: int) -> int:
    total: int = 0
    for i in range(n):
        total += i * i % 7
    return total


def alloc_work(n: int) -> list[bytes]:
    return [bytes(1024) for _ in range(n)]


def raise_work(n: int) -> None:
    for i in range(n):
        try:
            raise ValueError(i)
        except ValueError:
            pass


def worker(stop: threading.Event, shared_lock: threading.Lock) -> None:
    kept: list[list[bytes]] = []
    while not stop.is_set():
        cpu_work(200_000)
        kept.append(alloc_work(100))
        if len(kept) > 50:
            kept.pop(0)
        raise_work(100)
        with shared_lock:
            cpu_work(20_000)
        # A new lock each round, so the lock profiler sees it after a re-enable.
        with threading.Lock():
            pass


def collector_states() -> str:
    try:
        from ddtrace.profiling import bootstrap
        from ddtrace.profiling.bootstrap import sitecustomize
    except ImportError:
        return "profiler not loaded"

    profiler: Any = getattr(bootstrap, "profiler", None)
    if profiler is None:
        return "profiler not started"

    states: list[str] = []
    lock_states: set[str] = set()
    for col in profiler._collectors:
        name: str = type(col).__name__
        status: Any = getattr(col, "status", None)
        if status is not None:
            state: str = status.value
        else:
            state = "running" if sitecustomize._collector_running.get(id(col), True) else "stopped"
        if name.endswith(("LockCollector", "SemaphoreCollector", "ConditionCollector")):
            lock_states.add(state)
        else:
            states.append(f"{name}={state}")
    if lock_states:
        states.append(f"Lock={'/'.join(sorted(lock_states))}")
    return " ".join(states) or "no collector"


def main() -> None:
    stop: threading.Event = threading.Event()
    shared_lock: threading.Lock = threading.Lock()
    threads: list[threading.Thread] = [
        threading.Thread(target=worker, args=(stop, shared_lock), daemon=True) for _ in range(2)
    ]
    for t in threads:
        t.start()

    try:
        while True:
            print(time.strftime("%H:%M:%S"), collector_states(), flush=True)
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        for t in threads:
            t.join()


if __name__ == "__main__":
    main()
