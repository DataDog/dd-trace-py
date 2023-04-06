from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.debugging._capture.model import CaptureState
from ddtrace.debugging._capture.model import CapturedEvent
from ddtrace.debugging._encoding import BufferedEncoder
from ddtrace.debugging._metrics import metrics
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


CaptorType = Callable[[List[Tuple[str, Any]], List[Tuple[str, Any]], ExcInfoType, int], Any]

log = get_logger(__name__)
meter = metrics.get_meter("snapshot.collector")


NO_RETURN_VALUE = object()


class CapturedEventWithContext(object):
    """CapturedEvent with context.

    This is used to capture event for function invocation. the CapturedEventWithContext
    would call the event.enter() and event.exit() when the function starts and ends.

    The handler is triggered just after event.exit() is completed.
    """

    def __init__(
        self,
        event,  # type: CapturedEvent
        handler,  # type: Callable[[CapturedEvent],None]
    ):
        # type: (...) -> None
        self._on_exit_handler = handler
        self.event = event
        self.return_value = NO_RETURN_VALUE  # type: Any
        self.duration = None  # type: Optional[int]

        self.event.enter()

    def exit(self, retval, exc_info, duration_ns):
        # type: (Any, ExcInfoType, int) -> None
        """Exit the snapshot context.

        The arguments are used to record either the return value or the exception, and
        the duration of the wrapped call.
        """
        self.return_value = retval
        self.duration = duration_ns

        return self.__exit__(*exc_info)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        # type: (ExcInfoType) -> None
        self.event.exit(self.return_value, exc_info, self.duration)
        self._on_exit_handler(self.event)


class CapturedEventCollector(object):
    """Captured Event collector.

    This is used to collect and encode snapshot information as soon as
    requested. The ``push`` method is intended to be called in point
    event is over and information is already available and ready to be encoded
    or event status indicate it should be skipped.
    For function instrumentation (e.g. function probes), we use the ``attach`` method to create a
    ``CapturedEventWithContext`` instance that can be used to capture additional data,
    such as the return value of the wrapped function.
    """

    def __init__(self, encoder):
        # type: (BufferedEncoder) -> None
        self._encoder = encoder

    def _enqueue(self, snapshot):
        # type: (Any) -> None
        try:
            self._encoder.put(snapshot)
        except BufferFull:
            log.debug("Encoder buffer full")
            meter.increment("encoder.buffer.full")

    def push(self, event):
        # type: (CapturedEvent) -> None
        if event.state == CaptureState.SKIP_COND:
            meter.increment("skip", tags={"cause": "cond", "probe_id": event.probe.probe_id})
        elif event.state == CaptureState.SKIP_COND_ERROR:
            meter.increment("skip", tags={"cause": "cond_error", "probe_id": event.probe.probe_id})
        elif event.state == CaptureState.COND_ERROR_AND_COMMIT:
            meter.increment("skip", tags={"cause": "cond_error", "probe_id": event.probe.probe_id})
            self._enqueue(event)
        elif event.state == CaptureState.SKIP_RATE:
            meter.increment("skip", tags={"cause": "rate", "probe_id": event.probe.probe_id})
        elif event.state == CaptureState.DONE_AND_COMMIT:
            meter.increment("capture", tags={"probe_id": event.probe.probe_id})
            self._enqueue(event)

    def attach(self, event):
        # type: (CapturedEvent) -> CapturedEventWithContext
        """Collect via a snapshot context."""
        return CapturedEventWithContext(event, lambda e: self.push(e))
