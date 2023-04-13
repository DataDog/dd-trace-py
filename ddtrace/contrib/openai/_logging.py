import json
import threading
from typing import List
from typing import TypedDict

from ddtrace.internal.compat import get_connection_response
from ddtrace.internal.compat import httplib
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService


log = get_logger(__file__)


class V2LogEvent(TypedDict):
    """
    Note: these attribute names match the corresponding entry in the JSON payload.
    """

    message: str
    ddtags: str
    service: str
    hostname: str
    ddsource: str
    # Additional attributes can be specified on the event
    # including dd.trace_id and dd.span_id to correlate a trace


class V2LogWriter(PeriodicService):
    """
    v2/logs:
        - max payload size: 5MB
        - max single log: 1MB
        - max array size 1000

    refs:
        - https://docs.datadoghq.com/api/latest/logs/#send-logs
    """

    def __init__(self, site, api_key, interval, timeout):
        # type: (str, str, float, float) -> None
        super(V2LogWriter, self).__init__(interval=interval)
        self._lock = threading.Lock()
        # TODO: buffer limit
        self._buffer = []  # type: List[V2LogEvent]
        self._timeout = timeout  # type: float
        self._api_key = api_key  # type: str
        self._endpoint = "/api/v2/logs"  # type: str
        self._site = site  # type: str
        self._intake = "http-intake.logs.%s" % self._site  # type: str
        self._headers = {
            "DD-API-KEY": self._api_key,
            "Content-Type": "application/json",
        }

    def enqueue(self, log):
        # type: (V2LogEvent) -> None
        with self._lock:
            self._buffer.append(log)

    def periodic(self):
        # type: () -> None
        if not self._buffer:
            return

        num_logs = len(self._buffer)
        payload = json.dumps(self._buffer)
        self._buffer = []
        conn = httplib.HTTPSConnection(self._intake, 443, timeout=self._timeout)
        try:
            conn.request("POST", self._endpoint, payload, self._headers)
            resp = get_connection_response(conn)
            if resp.status >= 300:
                log.error("failed to send %d logs, got response code %r, status %r", num_logs, resp.status, resp.read())
            else:
                log.debug("sent %d logs to '%s%s'", num_logs, self._intake, self._endpoint)
        finally:
            conn.close()
