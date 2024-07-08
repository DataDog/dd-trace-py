from dataclasses import dataclass

import bm
import bm.utils as utils


@dataclass
class TracerParent:
    name: str
    depth: int


class Tracer(TracerParent, bm.Scenario):
    def run(self):
        # configure global tracer to drop traces rather than encoded and sent to
        # an agent
        from ddtrace import tracer

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
