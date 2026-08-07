import os
import signal
import time

from ddtrace import tracer
from ddtrace.internal._threads import periodic_threads
from ddtrace.internal.service import ServiceStatus
from ddtrace.profiling.profiler import Profiler


running = True


def stop(_signum: int, _frame: object) -> None:
    global running
    running = False


def profiler_running() -> bool:
    active = Profiler._active_instance
    return active is not None and active.status == ServiceStatus.RUNNING


def profiler_threads() -> list[str]:
    return sorted(
        thread.name for thread in periodic_threads.values() if thread.name == "ddtrace.profiling.scheduler:Scheduler"
    )


signal.signal(signal.SIGINT, stop)
signal.signal(signal.SIGTERM, stop)

last_state = profiler_running()
print(
    f"phase pid={os.getpid()} profiler_running={last_state} threads={profiler_threads()}",
    flush=True,
)

while running:
    with tracer.trace("profiling.rc.poc", resource="cpu-work") as span:
        deadline = time.monotonic() + 0.1
        value = 0
        while time.monotonic() < deadline:
            value = (value * 33 + 17) % 1_000_003
        span.set_tag("poc.value", str(value))

    state = profiler_running()
    if state != last_state:
        print(
            f"phase pid={os.getpid()} profiler_running={state} threads={profiler_threads()}",
            flush=True,
        )
        last_state = state
    time.sleep(0.9)

print(
    f"shutdown pid={os.getpid()} profiler_running={profiler_running()} threads={profiler_threads()}",
    flush=True,
)
tracer.shutdown()
