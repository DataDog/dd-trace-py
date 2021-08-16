import concurrent.futures

import bm

from ddtrace.internal.writer import AgentWriter


class DummyWriter(AgentWriter):
    def _send_payload(self, payload, count):
        # Do nothing
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
                    pass

    def run(self):
        # use DummyWriter to prevent network calls to the agent
        from ddtrace import tracer

        tracer.configure(writer=DummyWriter("http://localhost:8126/"))

        def _(loops):
            for _ in range(loops):
                with concurrent.futures.ThreadPoolExecutor(max_workers=self.nthreads) as executor:
                    tasks = {executor.submit(self.create_trace, tracer) for i in range(self.ntraces)}
                    for task in concurrent.futures.as_completed(tasks):
                        task.result()

        yield _
