import os

import bm
import bm.utils as utils
from opentelemetry.trace import get_tracer
from opentelemetry.trace import set_tracer_provider

from ddtrace import tracer
from ddtrace.opentelemetry import TracerProvider  # Requires ``ddtrace>=1.11``


set_tracer_provider(TracerProvider())
os.environ["OTEL_PYTHON_CONTEXT"] = "ddcontextvars_context"
otel_tracer = get_tracer(__name__)


utils.drop_traces(tracer)


class OtelSpan(bm.Scenario):
    nspans = bm.var(type=int)
    ntags = bm.var(type=int)
    ltags = bm.var(type=int)
    nmetrics = bm.var(type=int)
    finishspan = bm.var_bool()

    def run(self):
        # run scenario to also set tags on spans
        tags = utils.gen_tags(self)
        settags = len(tags) > 0

        # run scenario to also set metrics on spans
        metrics = utils.gen_metrics(self)
        setmetrics = len(metrics) > 0

        # run scenario to include finishing spans
        finishspan = self.finishspan

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
