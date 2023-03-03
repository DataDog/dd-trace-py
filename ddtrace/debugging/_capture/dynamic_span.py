from typing import Optional

import attr

import ddtrace
from ddtrace import Span
from ddtrace.debugging._capture.model import CaptureState
from ddtrace.debugging._capture.model import CapturedEvent
from ddtrace.debugging._metrics import probe_metrics
from ddtrace.debugging._probe.model import SpanFunctionProbe
from ddtrace.internal.metrics import Metrics


SPAN_NAME = "dd.dynamic.span"
PROBE_ID_TAG_NAME = "debugger.probeid"


@attr.s
class DynamicSpan(CapturedEvent):
    """wrapper for making a metric sample"""

    meter = attr.ib(type=Optional[Metrics.Meter], factory=lambda: probe_metrics.get_meter("probe"))
    message = attr.ib(type=Optional[str], default=None)
    duration = attr.ib(type=Optional[int], default=None)  # nanoseconds
    span = attr.ib(type=Span, default=None)

    def enter(self):
        if not isinstance(self.probe, SpanFunctionProbe):
            return

        probe = self.probe

        _args = dict(self.args) if self.args else {}
        if not self._eval_condition(_args):
            return

        self.span = ddtrace.tracer.trace(SPAN_NAME, resource=probe.func_qname)
        self.span.set_tags(probe.tags)
        self.span.set_tag(PROBE_ID_TAG_NAME, probe.probe_id)

        self.state = CaptureState.DONE

    def exit(self, retval, exc_info, duration):
        if not isinstance(self.probe, SpanFunctionProbe):
            return

        if self.span:
            self.span.__exit__(*exc_info)

    def line(self, _locals=None, exc_info=(None, None, None)):
        raise Exception("line span are not supported")
