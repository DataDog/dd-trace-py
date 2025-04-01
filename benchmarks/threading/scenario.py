import concurrent.futures
import random
from typing import Callable
from typing import Generator

import bm

from ddtrace.trace import TraceFilter
from ddtrace.trace import Tracer


class _DropTraces(TraceFilter):
    def process_trace(self, trace):
        return


class Threading(bm.Scenario):
    nthreads: int
    ntraces: int
    nspans: int

    def create_trace(self, tracer: Tracer) -> None:
        with tracer.trace("root"):
            for _ in range(self.nspans - 1):
                with tracer.trace("child"):
                    # Simulate work in each child
                    random.random()

    def run(self) -> Generator[Callable[[int], None], None, None]:
        from ddtrace.trace import tracer

        # configure global tracer to drop traces rather
        tracer.configure(trace_processors=[_DropTraces()])

        def _(loops: int) -> None:
            for _ in range(loops):
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.nthreads) as executor:
                    tasks = {executor.submit(self.create_trace, tracer) for i in range(self.ntraces)}
                    for task in concurrent.futures.as_completed(tasks):
                        task.result()

        yield _
