from threading import Thread
import time
from types import FrameType
from typing import Any
from typing import Callable
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.context import Context
from ddtrace.debugging._encoding import BufferedEncoder
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._probe.model import ConditionalProbe
from ddtrace.debugging._snapshot.model import ConditionEvaluationError
from ddtrace.debugging._snapshot.model import Snapshot
from ddtrace.internal._encoding import BufferFull
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger
from ddtrace.internal.rate_limiter import RateLimitExceeded


CaptorType = Callable[[List[Tuple[str, Any]], List[Tuple[str, Any]], ExcInfoType, int], Any]

log = get_logger(__name__)
meter = metrics.get_meter("snapshot.collector")


NO_RETURN_VALUE = object()


class SnapshotContext(object):
    def __init__(
        self,
        collector,  # type: SnapshotCollector
        probe,  # type: ConditionalProbe
        frame,  # type: FrameType
        thread,  # type: Thread
        args,  # type: List[Tuple[str, Any]]
        context,  # type: Optional[Context]
    ):
        # type: (...) -> None
        self.collector = collector
        self.args = args
        self.snapshot = None
        self.return_value = NO_RETURN_VALUE
        self._snapshot_encoder = collector._encoder._encoders[Snapshot]  # type: ignore[attr-defined]

        # TODO: Put rate limiting after condition evaluation
        if probe.limiter.limit() is RateLimitExceeded:
            return

        snapshot = Snapshot(
            probe=probe,
            frame=frame,
            thread=thread,
            exc_info=(None, None, None),
            context=context,
            timestamp=time.time(),
        )

        if snapshot.evaluate(dict(args)):
            self.snapshot = snapshot
            self.snapshot.entry_capture = self._snapshot_encoder.capture_context(
                args,
                [],
                (None, None, None),
                level=1,  # TODO: Retrieve from probe
            )

        self.snapshot = snapshot

    def exit(self, retval, exc_info):
        # type: (Any, ExcInfoType) -> None
        if self.snapshot is None:
            return

        self.return_value = retval
        return self.__exit__(*exc_info)

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        # type: (ExcInfoType) -> None
        if self.snapshot is None:
            return

        # If we get here it is because we're within the rate limits and the
        # probe condition evaluated to True.
        args = self.args
        _locals = (
            [("@return", self.return_value)] if self.return_value is not NO_RETURN_VALUE and exc_info[1] is None else []
        )  # type: List[Tuple[str, Any]]

        self.snapshot.return_capture = self._snapshot_encoder.capture_context(
            args,
            _locals,
            exc_info,
            level=1,  # TODO: Retrieve from probe
        )
        self.collector._enqueue(self.snapshot)
        meter.increment("encoded", tags={"probe_id": self.snapshot.probe.probe_id})
        log.debug("Encoded %r", self.snapshot)


class SnapshotCollector(object):
    """Snapshot collector.

    We push and do the bare minimum from the probe hook and delay the processing
    as late as possible so to reduce interference with customer's logic.
    """

    def __init__(self, encoder):
        # type: (BufferedEncoder) -> None
        self._encoder = encoder

    def _enqueue(self, snapshot):
        # type: (Snapshot) -> None
        try:
            self._encoder.put(snapshot)
        except BufferFull:
            log.debug("Encoder buffer full")
            meter.increment("encoder.buffer.full")

    def push(self, probe, frame, thread, exc_info, context=None):
        # type: (ConditionalProbe, FrameType, Thread, ExcInfoType, Optional[Context]) -> None
        """Push hook data to the collector."""
        snapshot = Snapshot(
            probe=probe,
            frame=frame,
            thread=thread,
            exc_info=exc_info,
            context=context,
            timestamp=time.time(),
        )
        try:
            if snapshot.evaluate():
                self._enqueue(snapshot)
                meter.increment("encoded", tags={"probe_id": probe.probe_id})
                log.debug("Encoded %r", snapshot)
            else:
                meter.increment("skip", tags={"cause": "cond", "probe_id": snapshot.probe.probe_id})
        except ConditionEvaluationError:
            log.error("Failed to evaluate condition for probe %s", snapshot.probe.probe_id, exc_info=True)
            meter.increment("skip", tags={"cause": "cond_exc", "probe_id": snapshot.probe.probe_id})

    def collect(self, probe, frame, thread, args, context=None):
        # type: (ConditionalProbe, FrameType, Thread, List[Tuple[str, Any]], Optional[Context]) -> SnapshotContext
        return SnapshotContext(self, probe, frame, thread, args, context)
