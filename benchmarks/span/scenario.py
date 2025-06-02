from bm import Scenario
import bm.utils as utils

from ddtrace import config
from ddtrace.constants import ERROR_MSG
from ddtrace.trace import tracer


class Span(Scenario):
    nspans: int
    ntags: int
    ltags: int
    nmetrics: int
    finishspan: bool
    traceid128: bool
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
        config._128_bit_trace_id_enabled = self.traceid128
        config._telemetry_enabled = config._telemetry_metrics_enabled = self.telemetry
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

        def _(loops):
            for _ in range(loops):
                for i in range(self.nspans):
                    s = tracer.start_span("test." + str(i))
                    if settags:
                        s.set_tags(tags)
                    if setmetrics:
                        s.set_metrics(metrics)
                    if add_event:
                        s._add_event("test.event", test_attributes)
                    if add_link:
                        s.set_link(trace_id=1, span_id=2)
                    if get_context:
                        _ = s.context
                    if is_recording:
                        _ = not s.finished
                    if record_exception:
                        s.record_exception(test_exception)
                    if set_status:
                        # Perform the equivalent operation for
                        # test_status = OtelStatus(OtelStatusCode.ERROR, "something went wrong")
                        s.error = 1
                        s.set_tag(ERROR_MSG, "something went wrong")
                    if update_name:
                        s.resource = "test.renamed." + str(i)
                    if finishspan:
                        s.finish()

        yield _
