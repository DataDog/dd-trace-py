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
from ddtrace.debugging._probe.model import ProbeConditionMixin
from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._signal.log import LogSignal
from ddtrace.debugging._signal.model import EvaluationError
from ddtrace.debugging._signal.model import SignalState
from ddtrace.debugging._signal.model import probe_to_signal
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.metrics import Metrics
from ddtrace.internal.rate_limiter import RateLimitExceeded


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
                value = float(probe.value(scope))
            except (DDExpressionEvaluationError, TypeError, ValueError) as e:
                # Report like a condition error: escaping here drops the failure silently.
                if isinstance(e, DDExpressionEvaluationError):
                    self.errors.append(EvaluationError(expr=e.dsl, message=e.error))
                else:
                    self.errors.append(EvaluationError(expr=probe.value.dsl, message=f"Value is not a number: {e}"))
                if cast(ProbeConditionMixin, self.probe).condition_error_limiter.limit() is RateLimitExceeded:
                    self.state = SignalState.SKIP_COND_ERROR
                else:
                    self.state = SignalState.COND_ERROR
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
