import math
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
    enable_cache: bool
    computation_intensity: int
    enable_sleep: bool
    sleep_duration: float
    nspans: int
    profiler_enabled: bool

    def cpu_intensive_computation(self, depth: int) -> int:
        """Very CPU intensive computation that can be cached"""
        result = 0
        
        iterations = self.computation_intensity + depth * 10
        
        for i in range(iterations):
            result += math.sin(i) * math.cos(i) + math.sqrt(abs(i * depth + 1))
            result += sum(j * j for j in range(10))
            
            temp_str = str(i * depth)
            result += len(temp_str.replace('0', '').replace('1', '').replace('2', ''))
        
        for n in range(depth + 50, depth + 100):
            is_prime = True
            for div in range(2, int(math.sqrt(n)) + 1):
                if n % div == 0:
                    is_prime = False
                    break
            if is_prime:
                result += n
        
        return int(result) % 1000000

    def recursive_traced_computation(self, depth: int = 0) -> int:
        """Recursively calls itself with tracing and optional caching"""
        with tracer.trace(f"recursive_computation.depth_{depth}") as span:
            span.set_tag("recursion.depth", depth)
            span.set_tag("recursion.max_depth", self.max_depth)
            span.set_tag("cache.enabled", self.enable_cache)
            span.set_tag("profiler.enabled", self.profiler_enabled)
            span.set_tag("component", "recursive_computation")
            
            if self.enable_cache and self.depth_cache and depth in self.depth_cache:
                result = self.depth_cache[depth]
                span.set_tag("cache.hit", True)
                span.set_metric("cache.size", len(self.depth_cache))
            else:
                span.set_tag("cache.hit", False)
                start_time = time.time()
                result = self.cpu_intensive_computation(depth)
                compute_time = time.time() - start_time
                
                span.set_metric("computation.time_ms", compute_time * 1000)
                span.set_metric("computation.result", result)
                
                if self.enable_cache and self.depth_cache is not None:
                    self.depth_cache[depth] = result
                    span.set_metric("cache.size", len(self.depth_cache))
            
            for i in range(self.nspans):
                with tracer.trace(f"computation.span_{i}"):
                    math.sqrt(i + depth)
            
            if depth < self.max_depth:
                child_result = self.recursive_traced_computation(depth + 1)
                span.set_metric("child.result", child_result)
                result += child_result
            elif self.enable_sleep:
                span.set_tag("action", "sleep_at_max_depth")
                time.sleep(self.sleep_duration)
            
            span.set_metric("final.result", result)
            return result

    def run(self) -> Generator[Callable[[int], None], None, None]:
        if self.profiler_enabled:
            os.environ.update({
                "DD_PROFILING_ENABLED": "1", 
            })
            import ddtrace.profiling.auto

        self.depth_cache = {} if self.enable_cache else None

        utils.drop_traces(tracer)
        utils.drop_telemetry_events()

        def _(loops: int) -> None:
            for _ in range(loops):
                if self.enable_cache:
                    self.depth_cache = {}
                
                self.recursive_traced_computation()

        yield _ 