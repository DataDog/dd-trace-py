from collections import ChainMap
import typing as t

from ddtrace.debugging._probe.model import ProbeConditionMixin
from ddtrace.debugging._probe.model import ProbeEvalTiming
from ddtrace.debugging._probe.model import RateLimitMixin
from ddtrace.debugging._probe.model import TimingMixin
from ddtrace.debugging._signal.model import Signal
from ddtrace.debugging._signal.model import SignalState
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.rate_limiter import RateLimitExceeded


class SignalContext:
    """Debugger signal context manager for function probes.

    Probes with an evaluation timing will trigger the following logic:

    - If the timing is on ENTRY, the probe will trigger on entry and on exit,
      but the condition and rate limiter will only be evaluated on entry.

    - If the timing is on EXIT, the probe will trigger on exit only, and the
      condition and rate limiter will be evaluated on exit.

    - IF the timing is DEFAULT, the probe will trigger on the default timing for
      the signal.

    - If the probe has no timing, it defaults to the ENTRY timing.
    """

    def __init__(self, signal: Signal) -> None:
        self.signal = signal
        self._timing = ProbeEvalTiming.ENTRY

        probe = signal.probe
        if isinstance(probe, TimingMixin):
            eval_at = probe.evaluate_at
            self._timing = signal.__default_timing__ if eval_at is ProbeEvalTiming.DEFAULT else eval_at

    def enter(self) -> None:
        signal = self.signal
        probe = signal.probe

        if self._timing is not ProbeEvalTiming.ENTRY:
            return

        scope = ChainMap(signal.args, signal.frame.f_globals)
        if isinstance(probe, ProbeConditionMixin) and not signal._eval_condition(scope):
            return

        if isinstance(probe, RateLimitMixin) and probe.limiter.limit() is RateLimitExceeded:
            signal.state = SignalState.SKIP_RATE
            return

        signal.enter(scope)

    def exit(self, retval: t.Any, exc_info: ExcInfoType, duration: int) -> Signal:
        """Exit the signal context.

        The arguments are used to record either the return value or the
        exception, and the duration of the wrapped call.
        """
        signal = self.signal
        if signal.state is not SignalState.NONE:
            # The signal has already been handled and move to a final state
            return signal

        probe = self.signal.probe

        frame = signal.frame
        extra: t.Dict[str, t.Any] = {"@duration": duration / 1e6}  # milliseconds

        exc = exc_info[1]
        if exc is not None:
            extra["@exception"] = exc
        else:
            extra["@return"] = retval

        scope = ChainMap(extra, frame.f_locals, frame.f_globals)

        if self._timing is ProbeEvalTiming.EXIT:
            # We only evaluate the condition and the rate limiter on exit if it
            # is a probe with timing on exit
            if isinstance(probe, ProbeConditionMixin) and not signal._eval_condition(scope):
                return signal

            if isinstance(probe, RateLimitMixin) and probe.limiter.limit() is RateLimitExceeded:
                signal.state = SignalState.SKIP_RATE
                return signal

        signal.exit(retval, t.cast(ExcInfoType, exc_info), duration or 0, scope)

        signal.state = SignalState.DONE

        return signal
