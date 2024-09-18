import concurrent.futures
import random
from typing import Callable
from typing import Generator
from typing import List
from typing import Optional

import bm

from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.internal.writer import TraceWriter


class NoopWriter(TraceWriter):
    def recreate(self) -> TraceWriter:
        return NoopWriter()

    def stop(self, timeout: Optional[float] = None) -> None:
        pass

    def write(self, spans: Optional[List[Span]] = None) -> None:
        pass

    def flush_queue(self) -> None:
        pass


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
        from ddtrace import tracer

        # configure global tracer to drop traces rather
        tracer.configure(writer=NoopWriter())

        def _(loops: int) -> None:
            for _ in range(loops):
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.nthreads) as executor:
                    tasks = {executor.submit(self.create_trace, tracer) for i in range(self.ntraces)}
                    for task in concurrent.futures.as_completed(tasks):
                        task.result()

        yield _
