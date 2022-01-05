import logging
from typing import ClassVar
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal import forksafe

from ...utils.formats import get_env
from ...utils.time import StopWatch
from ..agent import get_connection
from ..compat import get_connection_response
from ..compat import httplib
from ..encoding import JSONEncoderV2
from ..logger import get_logger
from ..periodic import PeriodicService
from .telemetry_request import TelemetryRequest
from .telemetry_request import app_closed_telemetry_request
from .telemetry_request import app_integrations_changed_telemetry_request
from .telemetry_request import app_started_telemetry_request


log = get_logger(__name__)


def _get_interval_or_default():
    return float(get_env("instrumentation_telemetry", "interval", default=60))


# TO DO: USE AGENT ENDPOINT
DEFAULT_TELEMETRY_ENDPOINT = "https://instrumentation-telemetry-intake.datadoghq.com"
DEFAULT_TELEMETRY_ENDPOINT_TEST = "https://all-http-intake.logs.datad0g.com/api/v2/apmtelemetry"


class TelemetryWriter(PeriodicService):
    """
    Periodic service which sends TelemetryRequest payloads to the agent-proxy [not yet but soon]
    """

    enabled = False  # type: ClassVar[bool]
    _instance = None  # type: ClassVar[Optional[TelemetryWriter]]
    _lock = forksafe.Lock()  # type: ClassVar[forksafe.ResetObject]
    sequence = 0  # type: ClassVar[int]

    def __init__(self, endpoint):
        # type: (str) -> None
        super(TelemetryWriter, self).__init__(interval=_get_interval_or_default())

        # switch endpoint to agent endpoint
        self.url = endpoint  # type: str
        self.encoder = JSONEncoderV2()
        self._events_queue = []  # type: List[TelemetryRequest]
        self._integrations_queue = []  # type: List[Dict]

    def _send_request(self, request):
        # type: (TelemetryRequest) -> httplib.HTTPResponse
        """
        Sends a telemetry request to telemetry intake [this will be switched the agent]
        """
        conn = get_connection(self.url)
        rb_json = self.encoder.encode(request["body"])
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

    def flush_integrations_queue(self):
        # type () -> List[Dict]
        """Returns a list of all integrations queued by integration_event"""
        with self._lock:
            integrations = self._integrations_queue
            self._integrations_queue = []
        return integrations

    def flush_events_queue(self):
        # type () -> List[TelemetryRequest]
        """Returns a list of all integrations queued by classmethods"""
        with self._lock:
            requests = self._events_queue
            self._events_queue = []
        return requests

    def queued_events(self):
        # type () -> List[TelemetryRequest]
        return self._events_queue

    def queued_integrations(self):
        # type () -> List[Dict]
        return self._integrations_queue

    def periodic(self):
        integrations = self.flush_integrations_queue()
        if integrations:
            integrations_request = app_integrations_changed_telemetry_request(integrations)
            TelemetryWriter.add_event(integrations_request)

        requests = self.flush_events_queue()
        requests_failed = []  # type: List[TelemetryRequest]
        for request in requests:
            resp = self._send_request(request)
            if resp.status >= 300:
                requests_failed.append(request)

        with self._lock:
            # add failed requests back to the requests queue
            self._events_queue.extend(requests_failed)

    def shutdown(self):
        # type: () -> None
        self.app_closed_event()
        self.periodic()

    @classmethod
    def add_event(cls, request):
        # type: (TelemetryRequest) -> None
        """
        Adds a Telemetry Request to the TelemetryWriter request buffer

         :param TelemetryRequest request: dictionary which stores a formatted telemetry request body and header
        """
        with cls._lock:
            if cls._instance is None:
                return
            request["body"]["seq_id"] = cls.sequence
            cls.sequence += 1
            # TO DO: replace list with a dead letter queue
            cls._instance._events_queue.append(request)

    @classmethod
    def integration_event(cls, integration_name):
        # type: (str) -> None
        """
        Creates and queues the names and settings of a patched module

        :param str integration_name: name of patched module
        """
        with cls._lock:
            if cls._instance is None:
                return

            integration = {
                "name": integration_name,
                "version": "",
                "enabled": True,
                "auto_enabled": True,
                "compatible": "",
                "error": "",
            }
            cls._instance._integrations_queue.append(integration)

    @classmethod
    def app_started_event(cls):
        # type: () -> None
        """
        Sent when TelemetryWriter is enabled or forks
        """
        request = app_started_telemetry_request()
        cls.add_event(request)

    @classmethod
    def app_closed_event(cls):
        # type: () -> None
        appclosed_request = app_closed_telemetry_request()
        cls.add_event(appclosed_request)

    @classmethod
    def _restart(cls):
        # type: () -> None
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
    def enable(cls, endpoint=DEFAULT_TELEMETRY_ENDPOINT):
        # type: (str) -> None
        with cls._lock:
            if cls._instance is not None:
                return

            telemetry_writer = cls(endpoint)
            telemetry_writer.start()

            forksafe.register(cls._restart)

            cls._instance = telemetry_writer
            cls.enabled = True

        cls.app_started_event()
