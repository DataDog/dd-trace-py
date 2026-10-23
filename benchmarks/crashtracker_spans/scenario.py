from bm import Scenario
import bm.utils as utils

from ddtrace.trace import tracer


class CrashtrackerSpans(Scenario):
    nspans: int
    crashtracking: bool

    def run(self):
        if self.crashtracking:
            from ddtrace.internal.core import crashtracking

            crashtracking.start()

        utils.drop_traces(tracer)
        utils.drop_telemetry_events()

        nspans = self.nspans

        def _(loops):
            for _ in range(loops):
                for i in range(nspans):
                    s = tracer.start_span("test." + str(i))
                    s.finish()

                if hasattr(tracer, "_span_aggregator"):
                    if hasattr(tracer._span_aggregator, "_traces"):
                        tracer._span_aggregator._traces.clear()

        yield _
