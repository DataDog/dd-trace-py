import os

import bm
import bm.utils as utils
from opentelemetry.trace import get_tracer
from opentelemetry.trace import set_tracer_provider

from ddtrace import config
from ddtrace.opentelemetry import TracerProvider  # Requires ``ddtrace>=1.11``
from ddtrace.trace import tracer


set_tracer_provider(TracerProvider())
os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"
otel_tracer = get_tracer(__name__)


class OtelSpan(bm.Scenario):
    nspans: int
    ntags: int
    ltags: int
    nmetrics: int
    finishspan: bool
    telemetry: bool

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
        config._telemetry_enabled = self.telemetry
        # Recreate span processors and configure global tracer to avoid sending traces to the agent
        utils.drop_traces(tracer)
        utils.drop_telemetry_events()

        def _(loops):
            for _ in range(loops):
                for i in range(self.nspans):
                    s = otel_tracer.start_span("test." + str(i))
                    if settags:
                        s.set_attributes(tags)
                    if setmetrics:
                        s.set_attributes(metrics)
                    if finishspan:
                        s.end()

        yield _

        if hasattr(trace, "_span_aggregator"):
            if hasattr(tracer._span_aggregator, "_traces"):
                # Clear traces to avoid memory leaks and errors on shutdown
                tracer._span_aggregator._traces.clear()
