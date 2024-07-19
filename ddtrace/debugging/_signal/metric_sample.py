from dataclasses import dataclass
from dataclasses import field
from typing import Optional
from typing import cast

from ddtrace.debugging._metrics import probe_metrics
from ddtrace.debugging._probe.model import MetricFunctionProbe
from ddtrace.debugging._probe.model import MetricProbeKind
from ddtrace.debugging._probe.model import MetricProbeMixin
from ddtrace.debugging._probe.model import ProbeEvaluateTimingForMethod
from ddtrace.debugging._signal.model import LogSignal
from ddtrace.debugging._signal.model import SignalState
from ddtrace.internal.metrics import Metrics


@dataclass
class MetricSample(LogSignal):
    """Wrapper for making a metric sample"""

    meter: Metrics.Meter = field(default_factory=lambda: probe_metrics.get_meter("probe"))

    def enter(self) -> None:
        if not isinstance(self.probe, MetricFunctionProbe):
            return

        probe = self.probe

        if probe.evaluate_at == ProbeEvaluateTimingForMethod.EXIT:
            return

        _args = dict(self.args) if self.args else {}
        if not self._eval_condition(_args):
            return

        self.sample(_args)
        self.state = SignalState.DONE

    def exit(self, retval, exc_info, duration) -> None:
        if not isinstance(self.probe, MetricFunctionProbe):
            return

        probe = self.probe
        _args = self._enrich_args(retval, exc_info, duration)

        if probe.evaluate_at != ProbeEvaluateTimingForMethod.EXIT:
            return
        if not self._eval_condition(_args):
            return

        self.sample(_args)
        self.state = SignalState.DONE

    def line(self) -> None:
        frame = self.frame

        if not self._eval_condition(frame.f_locals):
            return

        self.sample(frame.f_locals)
        self.state = SignalState.DONE

    def sample(self, _locals) -> None:
        tags = self.probe.tags
        probe = cast(MetricProbeMixin, self.probe)

        assert probe.kind is not None and probe.name is not None  # nosec

        value = float(probe.value(_locals)) if probe.value is not None else 1

        # TODO[perf]: We know the tags in advance so we can avoid the
        # list comprehension.
        if probe.kind == MetricProbeKind.COUNTER:
            self.meter.increment(probe.name, value, tags)
        elif probe.kind == MetricProbeKind.GAUGE:
            self.meter.gauge(probe.name, value, tags)
        elif probe.kind == MetricProbeKind.HISTOGRAM:
            self.meter.histogram(probe.name, value, tags)
        elif probe.kind == MetricProbeKind.DISTRIBUTION:
            self.meter.distribution(probe.name, value, tags)

    @property
    def message(self) -> Optional[str]:
        return f"Evaluation errors for probe id {self.probe.probe_id}" if self.errors else None

    def has_message(self) -> bool:
        return bool(self.errors)
