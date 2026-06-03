import bm
import bm.utils as utils


class Tracer(bm.Scenario):
    depth: int

    def run(self):
        # configure global tracer to drop traces rather than encoded and sent to
        # an agent
        from ddtrace.trace import tracer

        utils.drop_traces(tracer)
        utils.drop_telemetry_events()

        def _(loops):
            for _ in range(loops):
                spans = []
                for i in range(self.depth):
                    spans.append(tracer.trace(str(i)))
                while len(spans) > 0:
                    span = spans.pop()
                    span.finish()

        yield _
