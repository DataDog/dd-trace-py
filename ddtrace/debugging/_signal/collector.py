import os
from typing import Any
from typing import Callable
from typing import Optional

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


def _guardrail_tags(signal: Signal, reason: str, evaluation_kind: Optional[str] = None) -> dict[str, str]:
    # __type__ is each Signal subclass's own event_type -- see model.py,
    # log.py, snapshot.py, metric_sample.py, tracing.py, trigger.py.
    tags = {"reason": reason, "probe_id": signal.probe.probe_id}
    event_type = signal.__type__
    if event_type is not None:
        tags["event_type"] = event_type
    if evaluation_kind is not None:
        tags["evaluation_kind"] = evaluation_kind
    return tags


class SignalCollector:
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
            tags = {"reason": "queueFull", "probe_id": log_signal.probe.probe_id}
            event_type = log_signal.__type__
            if event_type is not None:
                tags["event_type"] = event_type
            meter.increment("dynamic_instrumentation.guardrails.events.dropped", tags=tags)
        except KeyError:
            log.error("No encoder for signal track %s", log_signal.__track__)

    def push(self, signal: Signal) -> None:
        if signal.state is SignalState.SKIP_COND:
            # Condition evaluated to False — not a guardrail event, no metric
            pass
        elif signal.state is SignalState.SKIP_COND_ERROR:
            # Error-throttle skips aren't part of the events.skipped reason
            # vocabulary (evaluationErrorThrottled) — no metric.
            pass
        elif signal.state is SignalState.COND_TIMEOUT:
            meter.increment(
                "dynamic_instrumentation.guardrails.events.skipped",
                tags=_guardrail_tags(signal, "evaluationTimeout", evaluation_kind="condition"),
            )
        elif signal.state is SignalState.COND_ERROR:
            meter.increment(
                "dynamic_instrumentation.guardrails.evaluation.errors",
                tags={"probe_type": type(signal.probe).__name__, "error_kind": "condition"},
            )
        elif signal.state is SignalState.SKIP_RATE_GLOBAL:
            meter.increment(
                "dynamic_instrumentation.guardrails.events.skipped",
                tags=_guardrail_tags(signal, "rateLimitGlobal"),
            )
        elif signal.state is SignalState.SKIP_RATE_PROBE:
            meter.increment(
                "dynamic_instrumentation.guardrails.events.skipped",
                tags=_guardrail_tags(signal, "rateLimitProbe"),
            )
        elif signal.state is SignalState.SKIP_BUDGET:
            # budgetExceededInvocation isn't part of the events.skipped
            # reason vocabulary — no metric.
            pass
        elif signal.state is SignalState.DONE:
            meter.increment("signal", tags={"probe_id": signal.probe.probe_id})

        # Emit evaluation duration if measured
        if signal._eval_duration_ms is not None:
            meter.distribution(
                "dynamic_instrumentation.guardrails.evaluation.duration",
                signal._eval_duration_ms,
                tags={"probe_type": type(signal.probe).__name__, "evaluation_kind": "condition"},
            )

        # Emit capture/template evaluation durations and capture.incomplete for snapshots
        if isinstance(signal, Snapshot):
            if signal._template_eval_duration_ms is not None:
                meter.distribution(
                    "dynamic_instrumentation.guardrails.evaluation.duration",
                    signal._template_eval_duration_ms,
                    tags={"probe_type": type(signal.probe).__name__, "evaluation_kind": "template"},
                )
            if signal._segment_timed_out:
                meter.increment(
                    "dynamic_instrumentation.guardrails.capture.incomplete",
                    tags=_guardrail_tags(signal, "timeout", evaluation_kind="template"),
                )
            if signal._capture_expr_error_reason is not None:
                meter.increment(
                    "dynamic_instrumentation.guardrails.capture.incomplete",
                    tags=_guardrail_tags(
                        signal, signal._capture_expr_error_reason, evaluation_kind="capture_expression"
                    ),
                )
            if signal._capture_incomplete_reason is not None:
                meter.increment(
                    "dynamic_instrumentation.guardrails.capture.incomplete",
                    tags=_guardrail_tags(signal, signal._capture_incomplete_reason),
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
            and signal.state in {SignalState.DONE, SignalState.COND_ERROR, SignalState.COND_TIMEOUT}
            and signal.has_message()
        ):
            log.debug("Enqueueing signal %s", signal)
            # This signal emits a log message
            self._enqueue(signal)
        else:
            log.debug(
                "Skipping signal %s (has message: %s)", signal, isinstance(signal, LogSignal) and signal.has_message()
            )
