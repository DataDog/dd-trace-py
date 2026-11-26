import http.client as httplib
from typing import TypedDict  # noqa:F401

from ddtrace.internal.logs import LogsIntakeWriter


class V2LogEvent(TypedDict):
    """
    Note: these attribute names match the corresponding entry in the JSON payload.
    """

    timestamp: int
    message: str
    ddtags: str
    service: str
    hostname: str
    ddsource: str
    status: str
    # Additional attributes can be specified on the event
    # including dd.trace_id and dd.span_id to correlate a trace


class V2LogWriter(LogsIntakeWriter):
    """Writer to the Datadog log intake.

    refs:
        - https://docs.datadoghq.com/api/v2/logs/#send-logs
    """

    _MAX_BUFFER_EVENTS = 1000

    def __init__(self, site: str, api_key: str, interval: float, timeout: float) -> None:
        self._intake = "http-intake.logs.%s" % site
        super().__init__(api_key=api_key, intake_url="https://%s" % self._intake, interval=interval, timeout=timeout)

    def _connect(self) -> httplib.HTTPSConnection:
        # Kept on http.client so the recorded-response tests can intercept requests.
        return httplib.HTTPSConnection(self._intake, 443, timeout=self._timeout)
