import os

import bm
import bm.utils as utils
from opentelemetry.trace import SpanContext
from opentelemetry.trace import get_tracer
from opentelemetry.trace import set_tracer_provider
from opentelemetry.trace.status import Status as OtelStatus
from opentelemetry.trace.status import StatusCode as OtelStatusCode

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
    add_event: bool
    add_link: bool
    get_context: bool
    is_recording: bool
    record_exception: bool
    set_status: bool
    update_name: bool

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
        add_event = self.add_event
        add_link = self.add_link
        get_context = self.get_context
        is_recording = self.is_recording
        record_exception = self.record_exception
        set_status = self.set_status
        update_name = self.update_name

        # Recreate span processors and configure global tracer to avoid sending traces to the agent
        utils.drop_traces(tracer)
        utils.drop_telemetry_events()

        # Pre-allocate all of the unique strings we'll need, that way the baseline memory overhead
        # is held constant throughout all tests.
        test_attributes = {"key": "value"}
        test_exception = RuntimeError("test_exception")
        test_link_context = SpanContext(trace_id=1, span_id=2, is_remote=False)
        test_status = OtelStatus(OtelStatusCode.ERROR, "something went wrong")

        def _(loops):
            for _ in range(loops):
                for i in range(self.nspans):
                    s = otel_tracer.start_span("test." + str(i))

                    if settags:
                        s.set_attributes(tags)
                    if setmetrics:
                        s.set_attributes(metrics)
                    if add_event:
                        s.add_event("test.event", test_attributes)
                    if add_link:
                        s.add_link(test_link_context)
                    if get_context:
                        _ = s.get_span_context()
                    if is_recording:
                        _ = s.is_recording()
                    if record_exception:
                        s.record_exception(test_exception)
                    if set_status:
                        s.set_status(test_status)
                    if update_name:
                        s.update_name("test.renamed." + str(i))
                    if finishspan:
                        s.end()

        yield _
