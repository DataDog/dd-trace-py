import os
from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.debugging._encoding import BufferedEncoder
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._signal.model import LogSignal
from ddtrace.debugging._signal.model import Signal
from ddtrace.debugging._signal.model import SignalState
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


CaptorType = Callable[[List[Tuple[str, Any]], List[Tuple[str, Any]], ExcInfoType, int], Any]

log = get_logger(__name__)
meter = metrics.get_meter("signal.collector")


NO_RETURN_VALUE = object()


class SignalContext(object):
    """Debugger signal context manager.

    This is used to capture data for function invocations. The SignalContext
    call  ``Signal.enter`` on entry ``Signal.exit`` on function return.

    The handler is triggered just after ``Signal.exit`` returns.
    """

    def __init__(
        self,
        signal: Signal,
        handler: Callable[[Signal], None],
    ) -> None:
        self._on_exit_handler = handler
        self.signal = signal
        self.return_value: Any = NO_RETURN_VALUE
        self.duration: Optional[int] = None

        self.signal.enter()

    def exit(self, retval: Any, exc_info: ExcInfoType, duration_ns: int) -> None:
        """Exit the snapshot context.

        The arguments are used to record either the return value or the exception, and
        the duration of the wrapped call.
        """
        self.return_value = retval
        self.duration = duration_ns

        return self.__exit__(*exc_info)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info: ExcInfoType) -> None:
        self.signal.exit(self.return_value, exc_info, self.duration)
        self._on_exit_handler(self.signal)


class SignalCollector(object):
    """Debugger signal collector.

    This is used to collect and encode signals emitted by probes as soon as
    requested. The ``push`` method is intended to be called after a line-level
    signal is fully emitted, and information is available and ready to be
    encoded, or the signal status indicate it should be skipped. For function
    instrumentation (e.g. function probes), we use the ``attach`` method to
    create a ``SignalContext`` instance that can be used to capture additional
    data, such as the return value of the wrapped function.
    """

    def __init__(self, encoder: BufferedEncoder) -> None:
        self._encoder = encoder

    def _enqueue(self, log_signal: LogSignal) -> None:
        try:
            log.debug(
                "[%s][P: %s] SignalCollector. _encoder (%s) _enqueue signal", os.getpid(), os.getppid(), self._encoder
            )
            self._encoder.put(log_signal)
        except BufferFull:
            log.debug("Encoder buffer full")
            meter.increment("encoder.buffer.full")

    def push(self, signal: Signal) -> None:
        if signal.state == SignalState.SKIP_COND:
            meter.increment("skip", tags={"cause": "cond", "probe_id": signal.probe.probe_id})
        elif signal.state in {SignalState.SKIP_COND_ERROR, SignalState.COND_ERROR}:
            meter.increment("skip", tags={"cause": "cond_error", "probe_id": signal.probe.probe_id})
        elif signal.state == SignalState.SKIP_RATE:
            meter.increment("skip", tags={"cause": "rate", "probe_id": signal.probe.probe_id})
        elif signal.state == SignalState.DONE:
            meter.increment("signal", tags={"probe_id": signal.probe.probe_id})

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

    def attach(self, signal: Signal) -> SignalContext:
        """Collect via a probe signal context manager."""
        return SignalContext(signal, lambda e: self.push(e))
