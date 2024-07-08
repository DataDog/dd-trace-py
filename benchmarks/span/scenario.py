from dataclasses import dataclass
from dataclasses import field

from bm import Scenario
from bm import var_bool
import bm.utils as utils

from ddtrace import config
from ddtrace import tracer


@dataclass
class SpanParent:
    name: str
    nspans: int
    ntags: int
    ltags: int
    nmetrics: int
    finishspan: bool = field(default_factory=var_bool)
    traceid128: bool = field(default_factory=var_bool)
    telemetry: bool = field(default_factory=var_bool)


class Span(SpanParent, Scenario):
    def run(self):
        # run scenario to also set tags on spans
        tags = utils.gen_tags(self)
        settags = len(tags) > 0

        # run scenario to also set metrics on spans
        metrics = utils.gen_metrics(self)
        setmetrics = len(metrics) > 0

        # run scenario to include finishing spans
        # Note - if finishspan is False the span will be gc'd when the SpanAggregrator._traces is reset
        # (ex: tracer.configure(filter) is called)
        finishspan = self.finishspan
        config._128_bit_trace_id_enabled = self.traceid128
        config._telemetry_enabled = config._telemetry_metrics_enabled = self.telemetry
        # Recreate span processors and configure global tracer to avoid sending traces to the agent
        utils.drop_traces(tracer)
        utils.drop_telemetry_events()

        def _(loops):
            for _ in range(loops):
                for i in range(self.nspans):
                    s = tracer.start_span("test." + str(i))
                    if settags:
                        s.set_tags(tags)
                    if setmetrics:
                        s.set_metrics(metrics)
                    if finishspan:
                        s.finish()

        yield _


# f = Span(
#     name="Span",
#     nspans=10,
#     ntags=10,
#     ltags=10,
#     nmetrics=10,
#     finishspan=True,
#     traceid128=True,
#     telemetry=True,
#     # bases=tuple(),
#     # namespace=dict(),
# )
