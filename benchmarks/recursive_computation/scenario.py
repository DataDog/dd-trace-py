import gc
import os
import time
from typing import Callable
from typing import Generator

import bm
import bm.utils as utils

from ddtrace.trace import tracer


class RecursiveComputation(bm.Scenario):
    name: str
    max_depth: int
    enable_sleep: bool
    sleep_duration: float
    profiler_enabled: bool
    gc_frames_enabled: bool
    force_cyclic_gc: bool

    def cpu_intensive_computation(self, depth: int) -> int:
        limit = 100 + (depth * 10)
        primes = []

        for num in range(2, limit):
            is_prime = True
            for i in range(2, int(num**0.5) + 1):
                if num % i == 0:
                    is_prime = False
                    break

            if is_prime:
                primes.append(num)

        return len(primes)

    def recursive_traced_computation(self, depth: int = 0) -> int:
        with tracer.trace(f"recursive_computation.depth_{depth}") as span:
            span.set_tag("recursion.depth", depth)
            span.set_tag("recursion.max_depth", self.max_depth)
            span.set_tag("profiler.enabled", self.profiler_enabled)
            span.set_tag("component", "recursive_computation")

            if depth % 3 == 0:
                start_time = time.time()
                result = self.cpu_intensive_computation(depth)
                compute_time = time.time() - start_time

                span.set_tag("computation.time_ms", compute_time * 1000)
                span.set_tag("computation.result", result)
            else:
                result = depth
                span.set_tag("computation.time_ms", 0)
                span.set_tag("computation.result", result)

            if depth < self.max_depth:
                child_result = self.recursive_traced_computation(depth + 1)
                span.set_tag("child.result", child_result)
                result += child_result
            elif self.force_cyclic_gc:
                cycle = []
                cycle.append(cycle)
                del cycle
                gc.collect()
            elif self.enable_sleep:
                span.set_tag("action", "sleep_at_max_depth")
                time.sleep(self.sleep_duration)

            span.set_tag("final.result", result)
            return result

    def run(self) -> Generator[Callable[[int], None], None, None]:
        if self.profiler_enabled:
            os.environ["DD_PROFILING_STACK_GC_ENABLED"] = str(self.gc_frames_enabled).lower()
            import ddtrace.profiling.auto  # noqa: F401

        utils.drop_traces(tracer)
        utils.drop_telemetry_events()

        def _(loops: int) -> None:
            for _ in range(loops):
                self.recursive_traced_computation()

        yield _
