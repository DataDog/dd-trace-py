import concurrent.futures
import random

import bm

from ddtrace.internal.writer import TraceWriter


class NoopWriter(TraceWriter):
    def recreate(self):
        # type: () -> TraceWriter
        return NoopWriter()

    def stop(self, timeout=None):
        # type: (Optional[float]) -> None
        pass

    def write(self, spans=None):
        # type: (Optional[List[Span]]) -> None
        pass


@bm.register
class Threading(bm.Scenario):
    nthreads = bm.var(type=int)
    ntraces = bm.var(type=int)
    nspans = bm.var(type=int)

    def create_trace(self, tracer):
        with tracer.trace("root"):
            for _ in range(self.nspans - 1):
                with tracer.trace("child"):
                    # Simulate work in each child
                    random.random()

    def run(self):
        from ddtrace import tracer

        # configure global tracer to drop traces rather
        tracer.configure(writer=NoopWriter())

        def _(loops):
            for _ in range(loops):
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.nthreads) as executor:
                    tasks = {executor.submit(self.create_trace, tracer) for i in range(self.ntraces)}
                    for task in concurrent.futures.as_completed(tasks):
                        task.result()

        yield _
