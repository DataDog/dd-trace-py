import json
import logging
from typing import ClassVar
from typing import List
from typing import Optional

from ddtrace.internal import forksafe

from ...utils.formats import get_env
from ...utils.time import StopWatch
from ..agent import get_connection
from ..compat import get_connection_response
from ..compat import httplib
from ..logger import get_logger
from ..periodic import PeriodicService
from .data import Integration
from .telemetry_request import TelemetryRequest
from .telemetry_request import app_closed_telemetry_request
from .telemetry_request import app_integrations_changed_telemetry_request


log = get_logger(__name__)


def _get_interval_or_default():
    return float(get_env("instrumentation_telemetry", "interval", default=60))


# TO DO: USE AGENT ENDPOINT
DEFAULT_TELEMETRY_ENDPOINT = "https://instrumentation-telemetry-intake.datadoghq.com"
DEFAULT_TELEMETRY_ENDPOINT_TEST = "https://all-http-intake.logs.datad0g.com/api/v2/apmtelemetry"


class TelemetryWriter(PeriodicService):
    enabled = False
    _instance = None  # type: ClassVar[Optional[TelemetryWriter]]
    _lock = forksafe.Lock()

    def __init__(self, endpoint=DEFAULT_TELEMETRY_ENDPOINT):
        # type: (str, int) -> None
        super(TelemetryWriter, self).__init__(interval=_get_interval_or_default())

        self.url = endpoint  # type: str
        self.sequence = 0  # type: int

        self.requests = []  # type: List[TelemetryRequest]
        self._integrations_request = None  # type: Optional[TelemetryRequest]

    def add_request(self, request):
        # type: (TelemetryRequest) -> None
        self.requests.append(request)

    def add_integration(self, integration):
        # type: (Integration) -> None
        if self._integrations_request:
            self._integrations_request["body"]["payload"]["integrations"].append(integration)
        else:
            integrations = [integration]
            self._integrations_request = app_integrations_changed_telemetry_request(integrations, 0)

    def _send_request(self, request):
        # type: (TelemetryRequest) -> httplib.HTTPResponse

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

    def periodic(self):
        # type: () -> None
        if self._integrations_request:
            self.add_request(self._integrations_request)
            self._integrations_request = None

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
        appclosed_request = app_closed_telemetry_request(self.sequence)
        self.requests.append(appclosed_request)
        self.periodic()

    @classmethod
    def get_instance(cls):
        # type: () -> Optional[TelemetryWriter]
        return cls._instance

    @classmethod
    def _restart(cls):
        cls.disable()
        cls.enable()

    @classmethod
    def disable(cls):
        # type: () -> None
        with cls._lock:
            if cls._instance is None:
                return

            forksafe.unregister(cls._restart)

            cls._instance.stop()
            cls._instance.join()
            cls._instance = None
            cls.enabled = False

    @classmethod
    def enable(cls):
        # type: () -> None
        with cls._lock:
            if cls._instance is not None:
                return

            telemetry_writer = cls()  # type: ignore[arg-type]
            telemetry_writer.start()

            forksafe.register(cls._restart)

            cls._instance = telemetry_writer
            cls.enabled = True
