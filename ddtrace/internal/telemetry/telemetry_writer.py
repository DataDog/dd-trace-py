import http
import json
import logging
from typing import Dict
from typing import List

from ...utils.time import StopWatch
from ..agent import get_connection
from ..compat import get_connection_response
from ..logger import get_logger
from ..periodic import PeriodicService
from .data.integration import Integration
from .data.payload import AppClosedPayload
from .data.payload import AppIntegrationsChangedPayload
from .data.telemetry_request import TelemetryRequest
from .data.telemetry_request import create_telemetry_request


# TO DO: USE AGENT ENDPOINT
DEFAULT_TELEMETRY_ENDPOINT = "https://instrumentation-telemetry-intake.datadoghq.com"
DEFAULT_TELEMETRY_ENDPOINT_TEST = "https://all-http-intake.logs.datad0g.com/api/v2/apmtelemetry"

log = get_logger(__name__)


class TelemetryWriter(PeriodicService):
    def __init__(self, endpoint=DEFAULT_TELEMETRY_ENDPOINT_TEST, interval=60):
        # type: (str, int) -> None
        super().__init__(interval=interval)

        self.url = endpoint  # type: str
        self.sequence = 0  # type: int

        self.requests = []  # type: List[TelemetryRequest]
        self.integrations_queue = []  # type: List[Integration]

    def add_request(self, request):
        # type: (TelemetryRequest) -> None
        self.requests.append(request)

    def add_integration(self, integration):
        # type: (Integration) -> None
        self.integrations_queue.append(integration)

    def _send_request(self, request):
        # type: (TelemetryRequest) -> http.client.HTTPResponse

        conn = get_connection(self.url)

        rb_json = json.dumps(request["body"])
        resp = None
        with StopWatch() as sw:
            try:
                conn.request("POST", self.url, rb_json, request["headers"])
                resp = get_connection_response(conn)

                t = sw.elapsed()
                if t >= self.interval:
                    log_level = logging.WARNING
                else:
                    log_level = logging.DEBUG

                log.log(log_level, "sent %d in %.5fs to %s. response: %s", len(rb_json), t, self.url, resp.status)
            finally:
                conn.close()

        return resp

    def _add_integrations_to_requests(self):
        # type: () -> None
        if self.integrations_queue:
            integrations_request = create_telemetry_request(AppIntegrationsChangedPayload(self.integrations_queue), 0)
            self.requests.append(integrations_request)
            self.integrations_queue = []

    def periodic(self):
        # type: () -> None
        self._add_integrations_to_requests()

        requests_failed = []  # type: List[TelemetryRequest]
        for request in self.requests:
            request["body"]["seq_id"] = self.sequence

            resp = self._send_request(request)
            if resp.status == 202:
                self.sequence += 1
            else:
                requests_failed.append(request)

        self.requests = requests_failed

    def shutdown(self):
        # type: () -> None
        appclosed_request = create_telemetry_request(AppClosedPayload(), 0)
        self.requests.append(appclosed_request)
        self.periodic()
