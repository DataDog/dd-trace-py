from typing import Optional

import tenacity

from ddtrace.debugging._config import config
from ddtrace.debugging._encoding import BufferedEncoder
from ddtrace.debugging._metrics import metrics
from ddtrace.internal import compat
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import AwakeablePeriodicService
from ddtrace.internal.utils.http import connector


log = get_logger(__name__)
meter = metrics.get_meter("snapshot.uploader")


class LogsIntakeUploaderV1(AwakeablePeriodicService):
    """Logs intake uploader.

    This class implements an interface with the debugger logs intake for both
    the debugger and the events platform.
    """

    ENDPOINT = config._snapshot_intake_endpoint

    RETRY_ATTEMPTS = 3

    def __init__(self, encoder, interval=None):
        # type: (BufferedEncoder, Optional[float]) -> None
        super(LogsIntakeUploaderV1, self).__init__(interval or config.upload_flush_interval)
        self._encoder = encoder
        self._headers = {
            "Content-type": "application/json; charset=utf-8",
            "Accept": "text/plain",
        }
        if config._tags_in_qs and config.tags:
            self.ENDPOINT += "?ddtags=" + config.tags
        self._connect = connector(config.snapshot_intake_url, timeout=config.upload_timeout)
        self._retry_upload = tenacity.Retrying(
            # Retry RETRY_ATTEMPTS times within the first half of the processing
            # interval, using a Fibonacci policy with jitter
            wait=tenacity.wait_random_exponential(
                multiplier=0.618 * self.interval / (1.618 ** self.RETRY_ATTEMPTS) / 2, exp_base=1.618
            ),
            stop=tenacity.stop_after_attempt(self.RETRY_ATTEMPTS),
        )

        log.debug(
            "Logs intake uploader initialized (url: %s, endpoint: %s, interval: %f)",
            config.snapshot_intake_url,
            self.ENDPOINT,
            self.interval,
        )

    def _write(self, payload):
        # type: (str) -> None
        try:
            with self._connect() as conn:
                conn.request(
                    "POST",
                    self.ENDPOINT,
                    payload,
                    headers=self._headers,
                )
                resp = compat.get_connection_response(conn)
                if resp.status != 200:
                    log.error("Failed to upload snapshot: [%d] %r", resp.status, resp.read())
                    meter.increment("upload.error", tags={"status": str(resp.status)})
                else:
                    meter.increment("upload.success")
                    meter.distribution("upload.size", len(payload))
                    log.debug("Snapshot uploaded: %s", payload)
        except Exception:
            log.error("Failed to write payload", exc_info=True)
            meter.increment("error")

    def upload(self):
        # type: () -> None
        """Upload request."""
        self.awake()

    def periodic(self):
        # type: () -> None
        """Upload the buffer content to the logs intake."""
        count = self._encoder.count
        if count:
            payload = self._encoder.encode()
            try:
                self._retry_upload(self._write, payload)
                meter.distribution("batch.cardinality", count)
            except Exception:
                log.debug("Cannot upload logs payload", exc_info=True)

    on_shutdown = periodic
