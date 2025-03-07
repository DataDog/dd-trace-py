import json
from queue import SimpleQueue as Queue
import time
import typing as t
from urllib.parse import quote

from ddtrace.debugging._config import di_config
from ddtrace.debugging._encoding import add_tags
from ddtrace.debugging._metrics import metrics
from ddtrace.debugging._probe.model import Probe
from ddtrace.internal import runtime
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.http import FormData
from ddtrace.internal.utils.http import connector
from ddtrace.internal.utils.http import multipart
from ddtrace.internal.utils.retry import fibonacci_backoff_with_jitter


log = get_logger(__name__)
meter = metrics.get_meter("probe.status")


ErrorInfo = t.Tuple[str, str]


class ProbeStatusLogger:
    RETRY_ATTEMPTS = 3
    RETRY_INTERVAL = 1
    ENDPOINT = "/debugger/v1/diagnostics"

    def __init__(self, service: str) -> None:
        self._service = service
        self._queue: Queue[str] = Queue()
        self._connect = connector(di_config._intake_url, timeout=di_config.upload_timeout)
        # Make it retryable
        self._write_payload_with_backoff = fibonacci_backoff_with_jitter(
            initial_wait=0.618 * self.RETRY_INTERVAL / (1.618**self.RETRY_ATTEMPTS) / 2,
            attempts=self.RETRY_ATTEMPTS,
        )(self._write_payload)

        if di_config._tags_in_qs and di_config.tags:
            self.ENDPOINT += f"?ddtags={quote(di_config.tags)}"

    def _payload(
        self, probe: Probe, status: str, message: str, timestamp: float, error: t.Optional[ErrorInfo] = None
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
                    "parentId": runtime.get_ancestor_runtime_id(),
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

    def _write_payload(self, data: t.Tuple[bytes, dict]) -> None:
        body, headers = data
        try:
            log.debug("Sending probe status payload: %r", body)
            with self._connect() as conn:
                conn.request(
                    "POST",
                    "/debugger/v1/diagnostics",
                    body,
                    headers=headers,
                )
                resp = conn.getresponse()
                if not (200 <= resp.status < 300):
                    log.error("Failed to upload payload: [%d] %r", resp.status, resp.read())
                    meter.increment("upload.error", tags={"status": str(resp.status)})
                else:
                    meter.increment("upload.success")
                    meter.distribution("upload.size", len(body))
        except Exception:
            log.error("Failed to write payload", exc_info=True)
            meter.increment("error")

    def _enqueue(self, probe: Probe, status: str, message: str, error: t.Optional[ErrorInfo] = None) -> None:
        self._queue.put_nowait(self._payload(probe, status, message, time.time(), error))
        log.debug("Probe status %s for probe %s enqueued", status, probe.probe_id)

    def flush(self) -> None:
        if self._queue.empty():
            return

        msgs: t.List[str] = []
        while not self._queue.empty():
            msgs.append(self._queue.get_nowait())

        try:
            self._write_payload_with_backoff(
                multipart(
                    parts=[
                        FormData(
                            name="event",
                            filename="event.json",
                            data=f"[{','.join(msgs)}]",
                            content_type="json",
                        )
                    ]
                )
            )
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
