from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field
from types import FrameType
from typing import Any
from typing import Optional
from typing import cast

from ddtrace.debugging._expressions import DDExpressionEvaluationError
from ddtrace.debugging._metrics import probe_metrics
from ddtrace.debugging._probe.model import MetricFunctionProbe
from ddtrace.debugging._probe.model import MetricLineProbe
from ddtrace.debugging._probe.model import MetricProbeKind
from ddtrace.debugging._probe.model import MetricProbeMixin
from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._safety import safe_qualname
from ddtrace.debugging._signal.log import LogSignal
from ddtrace.debugging._signal.model import probe_to_signal
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.metrics import Metrics


@dataclass
class MetricSample(LogSignal):
    """Wrapper for making a metric sample"""

    __default_timing__ = ProbeEvalTiming.ENTRY

    meter: Metrics.Meter = field(default_factory=lambda: probe_metrics.get_meter("probe"))

    def enter(self, scope: Mapping[str, Any]) -> None:
        if self._timing is ProbeEvalTiming.ENTRY:
            self.sample(scope)

    def exit(self, retval: Any, exc_info: ExcInfoType, duration: int, scope: Mapping[str, Any]) -> None:
        if self._timing is ProbeEvalTiming.EXIT:
            self.sample(scope)

    def line(self, scope: Mapping[str, Any]) -> None:
        self.sample(scope)

    def sample(self, scope: Mapping[str, Any]) -> None:
        tags = self.probe.tags
        probe = cast(MetricProbeMixin, self.probe)

        assert probe.kind is not None and probe.name is not None  # nosec

        if probe.value is None:
            value = 1.0
        else:
            try:
                raw = probe.value(scope)
            except DDExpressionEvaluationError as e:
                self._record_evaluation_error(e.dsl, e.error, kind="value")
                return
            try:
                value = float(raw)
            except Exception:
                # Name only the type: the value itself may be sensitive and has not been redacted.
                self._record_evaluation_error(
                    probe.value.dsl, f"Value is not a number: {safe_qualname(type(raw))}", kind="value"
                )
                return

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


@probe_to_signal.register
def _(
    probe: MetricFunctionProbe, frame: FrameType, thread: Any, trace_context: Any, meter: Metrics.Meter
) -> MetricSample:
    return MetricSample(probe=probe, frame=frame, thread=thread, trace_context=trace_context, meter=meter)


@probe_to_signal.register
def _(probe: MetricLineProbe, frame: FrameType, thread: Any, trace_context: Any, meter: Metrics.Meter) -> MetricSample:
    return MetricSample(probe=probe, frame=frame, thread=thread, trace_context=trace_context, meter=meter)
