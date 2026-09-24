"""Benchmark the production exception-profiler monitoring callback chain.

ExceptionCollector determines whether delivery uses a dedicated tool, the
shared Python dispatcher, or direct registration through the multiplexer.
Both versions execute the same sampler, traceback filtering, and ddup recording
code, so comparisons include the production callback rather than a Python stub.
"""

from collections.abc import Generator
import os
import tempfile
from typing import Callable

import bm


def _handled_exception(loops: int) -> None:
    for _ in range(loops):
        try:
            raise ValueError("bench")
        except ValueError:
            pass


def _stop_iteration(loops: int) -> None:
    for _ in range(loops):
        try:
            raise StopIteration
        except StopIteration:
            pass


def _explicit_reraise(loops: int) -> None:
    for _ in range(loops):
        try:
            try:
                raise ValueError("bench")
            except ValueError as exception:
                raise exception
        except ValueError:
            pass


def _bare_reraise(loops: int) -> None:
    for _ in range(loops):
        try:
            try:
                raise ValueError("bench")
            except ValueError:
                raise
        except ValueError:
            pass


_WORKLOADS: dict[str, Callable[[int], None]] = {
    "handled": _handled_exception,
    "stop_iteration": _stop_iteration,
    "explicit_reraise": _explicit_reraise,
    "bare_reraise": _bare_reraise,
}


class ExceptionProfilerMonitoring(bm.Scenario):  # type: ignore[misc]
    sampling_interval: int
    workload: str

    def run(self) -> Generator[Callable[[int], None], None]:
        from ddtrace.internal.datadog.profiling import ddup
        from ddtrace.profiling.collector.exception import ExceptionCollector

        if not ddup.is_available:
            raise RuntimeError("ddup is required for the exception-profiler monitoring benchmark")

        output_prefix = os.path.join(tempfile.gettempdir(), "ddtrace-exception-profiler-monitoring")
        ddup.config(
            env="benchmark", service="exception-profiler-monitoring", version="1", output_filename=output_prefix
        )
        ddup.start()

        workload = _WORKLOADS[self.workload]
        with ExceptionCollector(sampling_interval=self.sampling_interval, collect_message=False):
            yield workload
