from typing import Optional
from typing import cast

import attr

from ddtrace.debugging._capture.model import CaptureState
from ddtrace.debugging._capture.model import CapturedEvent
from ddtrace.debugging._metrics import probe_metrics
from ddtrace.debugging._probe.model import MetricFunctionProbe
from ddtrace.debugging._probe.model import MetricProbeKind
from ddtrace.debugging._probe.model import MetricProbeMixin
from ddtrace.debugging._probe.model import ProbeEvaluateTimingForMethod
from ddtrace.internal.metrics import Metrics


@attr.s
class MetricSample(CapturedEvent):
    """wrapper for making a metric sample"""

    meter = attr.ib(type=Optional[Metrics.Meter], factory=lambda: probe_metrics.get_meter("probe"))
    message = attr.ib(type=Optional[str], default=None)
    duration = attr.ib(type=Optional[int], default=None)  # nanoseconds

    def enter(self):
        if not isinstance(self.probe, MetricFunctionProbe):
            return

        probe = self.probe

        if probe.evaluate_at == ProbeEvaluateTimingForMethod.EXIT:
            return

        _args = dict(self.args) if self.args else {}
        if not self._eval_condition(_args):
            return

        self.sample(_args)
        self.state = CaptureState.DONE

    def exit(self, retval, exc_info, duration):
        if not isinstance(self.probe, MetricFunctionProbe):
            return

        probe = self.probe
        _args = self._enrich_args(retval, exc_info, duration)

        if probe.evaluate_at != ProbeEvaluateTimingForMethod.EXIT:
            return
        if not self._eval_condition(_args):
            return

        self.sample(_args)
        self.state = CaptureState.DONE

    def line(self):
        frame = self.frame

        if not self._eval_condition(frame.f_locals):
            return

        self.sample(frame.f_locals)
        self.state = CaptureState.DONE

    def sample(self, _locals):
        probe = cast(MetricProbeMixin, self.probe)

        assert probe.kind is not None and probe.name is not None

        value = float(probe.value(_locals)) if probe.value is not None else 1

        # TODO[perf]: We know the tags in advance so we can avoid the
        # list comprehension.
        if probe.kind == MetricProbeKind.COUNTER:
            self.meter.increment(probe.name, value, probe.tags)
        elif probe.kind == MetricProbeKind.GAUGE:
            self.meter.gauge(probe.name, value, probe.tags)
        elif probe.kind == MetricProbeKind.HISTOGRAM:
            self.meter.histogram(probe.name, value, probe.tags)
        elif probe.kind == MetricProbeKind.DISTRIBUTION:
            self.meter.distribution(probe.name, value, probe.tags)
