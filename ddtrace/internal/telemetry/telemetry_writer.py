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
from .metrics import Series
from .telemetry_request import TelemetryRequest
from .telemetry_request import app_closed_telemetry_request
from .telemetry_request import app_generate_metrics_telemetry_request
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
    sequence = 0  # type: int

    def __init__(self, endpoint=DEFAULT_TELEMETRY_ENDPOINT):
        # type: (str, int) -> None
        super(TelemetryWriter, self).__init__(interval=_get_interval_or_default())

        self.url = endpoint  # type: str
        
        self._requests_queue = []  # type: List[TelemetryRequest]
        self._integrations_queue = []  # type: List[Integration]
        self.series_map = []  # type Dict[str, Series]

    def _send_request(self, request):
        # type: (TelemetryRequest) -> httplib.HTTPResponse
        """
        Sends a telemetry request to telemetry intake [this will be switched the agent]
        """

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
        with self._lock:
            # copy requests and integrations queued by classmethods to a local variable
            requests = self._requests_queue
            integrations = self._integrations_queue
            series = self.series_map.values()
            self._requests_queue = []
            self._integrations_queue = []
            self.series_map = {}  # type: Dict[str, Series]

        if series:
            metrics_request = app_generate_metrics_telemetry_request(series)
            self.add_request(metrics_request)

        if integrations:
            integrations_request = app_integrations_changed_telemetry_request(integrations)
            self.add_request(integrations_request)

        requests_failed = []  # type: List[TelemetryRequest]
        for request in requests:
            resp = self._send_request(request)
            if resp.status >= 300:
                requests_failed.append(request)

        with self._lock:
            # add failed requests back to the requests queue
            self._requests_queue.extend(requests_failed)

    def shutdown(self):
        # type: () -> None
        appclosed_request = app_closed_telemetry_request()
        self.add_request(appclosed_request)
        self.periodic()

    @classmethod
    def add_request(cls, request):
        # type: (TelemetryRequest) -> None
        """
        Adds a Telemetry Request to the TelemetryWriter request buffer

         :param TelemetryRequest request: dictionary which stores a formatted telemetry request body and header
        """
        with cls._lock:
            if cls._instance:
                request["body"]["seq_id"] = cls.sequence
                cls.sequence += 1
                cls._instance._requests_queue.append(request)

    @classmethod
    def add_integration(cls, integration):
        # type: (Integration) -> None
        """
        Generates a Telemetry request with an AppIntegrationsChangedEvent payload
          - If an integrations changed event exists then it add the integation
            to the integrations buffers

        :param Integration integration: dictionary which stores the name and configurations of a dd-trace Integration
        """
        with cls._lock:
            if cls._instance is None:
                return
            cls._instance._integrations_queue.append(integration)

    @classmethod
    def add_metric(cls, series):
        # type: (Series) -> None
        """
        Add series object to series map. If the metric already
        exists in the series map then update its values.
        """
        with cls._lock:
            if cls._instance is None:
                return

            writer_instance = cls._instance
            metric_name = series.metric
            if metric_name in writer_instance.series_map:
                current_series = writer_instance.series_map[metric_name]
                current_series.combine(series)
                return

            writer_instance.series_map[metric_name] = series

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
