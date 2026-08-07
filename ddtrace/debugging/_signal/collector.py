import os
from typing import Any
from typing import Callable

from ddtrace.debugging._encoding import BufferedEncoder
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._signal.log import LogSignal
from ddtrace.debugging._signal.model import Signal
from ddtrace.debugging._signal.model import SignalState
from ddtrace.debugging._signal.model import SignalTrack
from ddtrace.debugging._signal.snapshot import Snapshot
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


CaptorType = Callable[[list[tuple[str, Any]], list[tuple[str, Any]], ExcInfoType, int], Any]

log = get_logger(__name__)
meter = metrics.get_meter("signal.collector")


class SignalCollector(object):
    """Debugger signal collector.

    This is used to collect and encode signals emitted by probes as soon as
    requested. The ``push`` method is intended to be called after a line-level
    signal is fully emitted, and information is available and ready to be
    encoded, or the signal status indicate it should be skipped.
    """

    def __init__(self, tracks: dict[SignalTrack, BufferedEncoder]) -> None:
        self._tracks = tracks

    def _enqueue(self, log_signal: LogSignal) -> None:
        try:
            log.debug(
                "[%s][P: %s] SignalCollector enqueue signal on track %s",
                os.getpid(),
                os.getppid(),
                log_signal.__track__,
            )
            self._tracks[log_signal.__track__].put(log_signal)
        except BufferFull:
            log.debug("Encoder buffer full")
            meter.increment(
                "dynamic_instrumentation.guardrails.events.dropped",
                tags={"reason": "queueFull", "event_type": "snapshot"},
            )
        except KeyError:
            log.error("No encoder for signal track %s", log_signal.__track__)

    def push(self, signal: Signal) -> None:
        if signal.state is SignalState.SKIP_COND:
            # Condition evaluated to False — not a guardrail event, no metric
            pass
        elif signal.state is SignalState.SKIP_COND_ERROR:
            meter.increment(
                "dynamic_instrumentation.guardrails.events.skipped",
                tags={"reason": "evaluationErrorThrottled", "probe_type": type(signal.probe).__name__},
            )
        elif signal.state is SignalState.COND_ERROR:
            meter.increment(
                "dynamic_instrumentation.guardrails.evaluation.errors",
                tags={"probe_type": type(signal.probe).__name__, "error_kind": "condition"},
            )
        elif signal.state is SignalState.SKIP_RATE_GLOBAL:
            meter.increment(
                "dynamic_instrumentation.guardrails.events.skipped",
                tags={"reason": "rateLimitGlobal", "probe_type": type(signal.probe).__name__},
            )
        elif signal.state is SignalState.SKIP_RATE_PROBE:
            meter.increment(
                "dynamic_instrumentation.guardrails.events.skipped",
                tags={"reason": "rateLimitProbe", "probe_type": type(signal.probe).__name__},
            )
        elif signal.state is SignalState.SKIP_BUDGET:
            meter.increment(
                "dynamic_instrumentation.guardrails.events.skipped",
                tags={"reason": "budgetExceededInvocation", "probe_type": type(signal.probe).__name__},
            )
        elif signal.state is SignalState.DONE:
            meter.increment("signal", tags={"probe_id": signal.probe.probe_id})

        # Emit evaluation duration if measured
        if signal._eval_duration_ms is not None:
            meter.distribution(
                "dynamic_instrumentation.guardrails.evaluation.duration",
                signal._eval_duration_ms,
                tags={"probe_type": type(signal.probe).__name__, "evaluation_kind": "condition"},
            )

        # Emit capture and template evaluation durations for snapshots
        if isinstance(signal, Snapshot):
            if signal._template_eval_duration_ms is not None:
                meter.distribution(
                    "dynamic_instrumentation.guardrails.evaluation.duration",
                    signal._template_eval_duration_ms,
                    tags={"probe_type": type(signal.probe).__name__, "evaluation_kind": "template"},
                )
            if signal._capture_duration_ms is not None:
                truncated = "true" if signal.errors else "false"
                meter.distribution(
                    "dynamic_instrumentation.guardrails.capture.duration",
                    signal._capture_duration_ms,
                    tags={"probe_type": type(signal.probe).__name__, "truncated": truncated},
                )

        if (
            isinstance(signal, LogSignal)
            and signal.state in {SignalState.DONE, SignalState.COND_ERROR}
            and signal.has_message()
        ):
            log.debug("Enqueueing signal %s", signal)
            # This signal emits a log message
            self._enqueue(signal)
        else:
            log.debug(
                "Skipping signal %s (has message: %s)", signal, isinstance(signal, LogSignal) and signal.has_message()
            )
