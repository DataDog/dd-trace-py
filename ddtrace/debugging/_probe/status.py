import json
from queue import SimpleQueue as Queue
import time
import typing as t

from ddtrace.debugging._encoding import add_tags
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._probe.model import Probe
from ddtrace.debugging._uploader import build_debugger_sender
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import DebuggerTrackType
from ddtrace.internal.runtime import get_ancestor_runtime_id
from ddtrace.internal.runtime import get_runtime_id
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter


log = get_logger(__name__)
meter = metrics.get_meter("probe.status")


ErrorInfo = tuple[str, str]


class ProbeStatusLogger:
    RETRY_ATTEMPTS = 3
    RETRY_INTERVAL = 1

    def __init__(self, service: str) -> None:
        self._service = service
        self._queue: Queue[str] = Queue()
        self._sender = build_debugger_sender()
        # Make it retryable
        self._write_payload_with_backoff = fibonacci_backoff_with_jitter(
            initial_wait=0.618 * self.RETRY_INTERVAL / (1.618**self.RETRY_ATTEMPTS) / 2,
            attempts=self.RETRY_ATTEMPTS,
        )(self._write_payload)

    def _payload(
        self, probe: Probe, status: str, message: str, timestamp: float, error: t.Optional[ErrorInfo] = None
    ) -> str:
        payload = {
            "service": self._service,
            "timestamp": int(timestamp * 1e3),  # milliseconds
            "message": message,
            "ddsource": "dd_debugger",
            "type": "diagnostic",
            "debugger": {
                "diagnostics": {
                    "probeId": probe.probe_id,
                    "probeVersion": probe.version,
                    "runtimeId": get_runtime_id(),
                    "parentId": get_ancestor_runtime_id(),
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

    def _write_payload(self, body: bytes) -> None:
        try:
            log.debug("Sending probe status payload: %r", body)
            response = self._sender.send(body, DebuggerTrackType.Diagnostics)
        except Exception:
            log.error("Failed to write payload", exc_info=True)
            meter.increment("error")
            return

        if not response.accepted:
            log.error("Failed to upload payload: [%d] %r", response.status, response.body)
            meter.increment("upload.error", tags={"status": str(response.status)})
            return

        meter.increment("upload.success")
        meter.distribution("upload.size", len(body))

    def _enqueue(self, probe: Probe, status: str, message: str, error: t.Optional[ErrorInfo] = None) -> None:
        self._queue.put_nowait(self._payload(probe, status, message, time.time(), error))
        log.debug("Probe status %s for probe %s enqueued", status, probe.probe_id)

    def flush(self) -> None:
        if self._queue.empty():
            return

        msgs: list[str] = []
        while not self._queue.empty():
            msgs.append(self._queue.get_nowait())

        try:
            self._write_payload_with_backoff(f"[{','.join(msgs)}]".encode("utf-8"))
        except Exception:
            log.error("Failed to write probe status after retries", exc_info=True)

    def received(self, probe: Probe, message: t.Optional[str] = None) -> None:
        self._enqueue(
            probe,
            "RECEIVED",
            message or "Probe %s has been received correctly" % probe.probe_id,
        )

    def installed(self, probe: Probe, message: t.Optional[str] = None) -> None:
        self._enqueue(
            probe,
            "INSTALLED",
            message or "Probe %s instrumented correctly" % probe.probe_id,
        )

    def emitting(self, probe: Probe, message: t.Optional[str] = None) -> None:
        self._enqueue(
            probe,
            "EMITTING",
            message or "Probe %s is emitting data" % probe.probe_id,
        )

    def error(self, probe: Probe, error: t.Optional[ErrorInfo] = None) -> None:
        self._enqueue(probe, "ERROR", "Failed to instrument probe %s" % probe.probe_id, error)
