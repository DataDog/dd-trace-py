from collections import deque
import json
import time
from typing import Optional
from typing import Tuple

from ddtrace.debugging._config import di_config
from ddtrace.debugging._encoding import BufferedEncoder
from ddtrace.debugging._encoding import BufferFull
from ddtrace.debugging._encoding import add_tags
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._probe.model import Probe
from ddtrace.internal import runtime
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
meter = metrics.get_meter("probe.status")


ErrorInfo = Tuple[str, str]


class ProbeStatusLogger(object):
    def __init__(self, service: str, encoder: BufferedEncoder) -> None:
        self._service = service
        self._encoder = encoder
        self._retry_queue: deque[Tuple[str, float]] = deque()

    def _payload(
        self, probe: Probe, status: str, message: str, timestamp: float, error: Optional[ErrorInfo] = None
    ) -> str:
        payload = {
            "service": self._service,
            "timestamp": int(timestamp * 1e3),  # milliseconds
            "message": message,
            "ddsource": "dd_debugger",
            "debugger": {
                "diagnostics": {
                    "probeId": probe.probe_id,
                    "probeVersion": probe.version,
                    "runtimeId": runtime.get_runtime_id(),
                    "status": status,
                }
            },
        }

        add_tags(payload)

        if error is not None:
            error_type, message = error
            payload["debugger"]["diagnostics"]["exception"] = {  # type: ignore[index]
                "type": error_type,
                "message": message,
            }

        return json.dumps(payload)

    def _write(self, probe: Probe, status: str, message: str, error: Optional[ErrorInfo] = None) -> None:
        if self._retry_queue:
            meter.distribution("backlog.size", len(self._retry_queue))

        try:
            now = time.time()

            while self._retry_queue:
                item, ts = self._retry_queue.popleft()
                if now - ts > di_config.diagnostics_interval:
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

            payload = self._payload(probe, status, message, now, error)

            try:
                self._encoder.put(payload)
            except BufferFull:
                log.warning("Failed to write status message to the buffer because it is full. Enqueuing for later.")
                meter.increment("buffer_full")
                self._retry_queue.append((payload, now))

        except Exception:
            log.error("Failed to write probe status payload", exc_info=True)

    def received(self, probe: Probe, message: Optional[str] = None) -> None:
        self._write(
            probe,
            "RECEIVED",
            message or "Probe %s has been received correctly" % probe.probe_id,
        )

    def installed(self, probe: Probe, message: Optional[str] = None) -> None:
        self._write(
            probe,
            "INSTALLED",
            message or "Probe %s instrumented correctly" % probe.probe_id,
        )

    def error(self, probe: Probe, error: Optional[ErrorInfo] = None) -> None:
        self._write(probe, "ERROR", "Failed to instrument probe %s" % probe.probe_id, error)
