from collections import deque
import json
import time
from typing import Optional
from typing import Tuple

from ddtrace.debugging._config import config
from ddtrace.debugging._encoding import BufferFull
from ddtrace.debugging._encoding import BufferedEncoder
from ddtrace.debugging._encoding import _unwind_stack
from ddtrace.debugging._encoding import add_tags
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._probe.model import Probe
from ddtrace.internal.compat import ExcInfoType
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
meter = metrics.get_meter("probe.status")


class ProbeStatusLogger(object):
    ENDPOINT = config.snapshot_intake_url

    def __init__(self, service, encoder):
        # type: (str, BufferedEncoder) -> None
        self._service = service
        self._encoder = encoder
        self._retry_queue = deque()  # type: deque[Tuple[str, float]]

    def _payload(self, probe, status, message, exc_info=None):
        # type: (Probe, str, str, Optional[ExcInfoType]) -> str
        payload = {
            "service": self._service,
            "message": message,
            "ddsource": "dd_debugger",
            "debugger": {
                "diagnostics": {
                    "probeId": probe.probe_id,
                    "status": status,
                }
            },
        }

        add_tags(payload)

        if exc_info is not None:
            exc_type, exc, tb = exc_info
            assert exc_type is not None and tb is not None, exc_info
            payload["debugger"]["diagnostics"]["exception"] = {  # type: ignore[index]
                "type": exc_type.__name__,
                "message": str(exc),
                "stacktrace": _unwind_stack(tb.tb_frame),
            }

        return json.dumps(payload)

    def _write(self, probe, status, message, exc_info=None):
        # type: (Probe, str, str, Optional[ExcInfoType]) -> None
        if self._retry_queue:
            meter.distribution("backlog.size", len(self._retry_queue))

        try:
            now = time.time()

            while self._retry_queue:
                item, ts = self._retry_queue.popleft()
                if now - ts > config.diagnostic_interval:
                    # We discard the expired items as they wouldn't be picked
                    # up by the backend anyway.
                    continue

                try:
                    self._encoder.put(item)
                except BufferFull:
                    self._retry_queue.appendleft((item, ts))
                    log.warning("Failed to clear probe status message backlog. Will try again later.")
                    meter.increment("backlog.buffer_full")
                    return

            payload = self._payload(probe, status, message, exc_info)

            try:
                self._encoder.put(payload)
            except BufferFull:
                log.warning("Failed to write status message to the buffer because it is full. Enqueuing for later.")
                meter.increment("buffer_full")
                self._retry_queue.append((payload, now))

        except Exception:
            log.error("Failed to write probe status payload", exc_info=True)

    def received(self, probe, message=None):
        # type: (Probe, Optional[str]) -> None
        self._write(
            probe,
            "RECEIVED",
            message or "Probe %s has been received correctly" % probe.probe_id,
        )

    def installed(self, probe, message=None):
        # type: (Probe, Optional[str]) -> None
        self._write(
            probe,
            "INSTALLED",
            message or "Probe %s instrumented correctly" % probe.probe_id,
        )

    def error(self, probe, message=None, exc_info=None):
        # type: (Probe, Optional[str], Optional[ExcInfoType]) -> None
        if message is None and exc_info is None:
            raise ValueError("Either message or exc_info must be provided")

        if exc_info is not None and message is None:
            _, exc, _ = exc_info
            message = "Probe %s instrumentation failed: %r" % (probe.probe_id, exc)

        self._write(probe, "ERROR", message, exc_info)  # type: ignore[arg-type]
