import concurrent.futures
import random
from typing import Callable  # noqa:F401
from typing import Generator  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401

import bm

from ddtrace._trace.span import Span  # noqa:F401
from ddtrace._trace.tracer import Tracer  # noqa:F401
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

    def flush_queue(self):
        # type: () -> None
        pass


class Threading(bm.Scenario):
    nthreads = bm.var(type=int)
    ntraces = bm.var(type=int)
    nspans = bm.var(type=int)

    def create_trace(self, tracer):
        # type: (Tracer) -> None
        with tracer.trace("root"):
            for _ in range(self.nspans - 1):
                with tracer.trace("child"):
                    # Simulate work in each child
                    random.random()

    def run(self):
        # type: () -> Generator[Callable[[int], None], None, None]
        from ddtrace import tracer

        # configure global tracer to drop traces rather
        tracer.configure(writer=NoopWriter())

        def _(loops):
            # type: (int) -> None
            for _ in range(loops):
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.nthreads) as executor:
                    tasks = {executor.submit(self.create_trace, tracer) for i in range(self.ntraces)}
                    for task in concurrent.futures.as_completed(tasks):
                        task.result()

        yield _
