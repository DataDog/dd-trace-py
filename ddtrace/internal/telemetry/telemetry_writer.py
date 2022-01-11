from collections import namedtuple
import logging
from typing import ClassVar
from typing import Dict
from typing import List
from typing import Optional

from ddtrace.internal import forksafe

from ..agent import get_connection
from ..agent import get_trace_url
from ..compat import get_connection_response
from ..compat import httplib
from ..encoding import JSONEncoderV2
from ..logger import get_logger
from ..periodic import PeriodicService
from ..utils.formats import get_env
from ..utils.time import StopWatch
from .telemetry_request import create_telemetry_request


TelemetryRequest = namedtuple("TelemetryRequest", "request fail_count")

log = get_logger(__name__)


def _get_interval_or_default():
    return float(get_env("instrumentation_telemetry", "interval", default=60))


class TelemetryWriter(PeriodicService):
    """
    Periodic service which sends Telemetry request payloads to the agent-proxy [not yet but soon]
    """

    AGENT_URL = get_trace_url()
    ENDPOINT = "telemetry/proxy/api/v2/apmtelemetry"
    MAX_FAIL_COUNT = 3

    enabled = False  # type: ClassVar[bool]
    _instance = None  # type: ClassVar[Optional[TelemetryWriter]]
    _lock = forksafe.Lock()  # type: ClassVar[forksafe.ResetObject]
    sequence = 0  # type: ClassVar[int]

    def __init__(self):
        # type: () -> None
        super(TelemetryWriter, self).__init__(interval=_get_interval_or_default())

        self.encoder = JSONEncoderV2()
        self._events_queue = []  # type: List[TelemetryRequest]
        self._integrations_queue = []  # type: List[Dict]

    def _get_headers(self, payload_type):
        # type: (str) -> Dict
        """
        Creates request headers
        """
        return {
            "Content-type": "application/json",
            "DD-Telemetry-Request-Type": payload_type,
            "DD-Telemetry-API-Version": "v2",
        }

    def _send_request(self, request):
        # type: (Dict) -> httplib.HTTPResponse
        """
        Sends a telemetry request to the trace agent
        """
        conn = get_connection(self.AGENT_URL)
        rb_json = self.encoder.encode(request)
        resp = None
        with StopWatch() as sw:
            try:
                conn.request("POST", self.ENDPOINT, rb_json, self._get_headers(request["request_type"]))
                resp = get_connection_response(conn)

                t = sw.elapsed()
                if t >= self.interval:
                    log_level = logging.WARNING
                else:
                    log_level = logging.DEBUG

                log.log(
                    log_level,
                    "sent %d in %.5fs to %s/%s. response: %s",
                    len(rb_json),
                    t,
                    self.AGENT_URL,
                    self.ENDPOINT,
                    resp.status,
                )
            finally:
                conn.close()
        return resp

    def flush_integrations_queue(self):
        # type () -> List[Dict]
        """Returns a list of all integrations queued by add_integration"""
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

    def periodic(self):
        integrations = self.flush_integrations_queue()
        if integrations:
            self.app_integrations_changed_event(integrations)

        telemetry_requests = self.flush_events_queue()

        requests_failed = []  # type: List[TelemetryRequest]
        for telemetry_request in telemetry_requests:
            request, fail_count = telemetry_request
            resp = self._send_request(request)
            if resp.status >= 300 and fail_count + 1 < self.MAX_FAIL_COUNT:
                requests_failed.append(TelemetryRequest(request, fail_count + 1))

        if requests_failed:
            with self._lock:
                # add failed requests back to the requests queue
                self._events_queue.extend(requests_failed)

    def shutdown(self):
        # type: () -> None
        self.app_closed_event()
        self.periodic()

    @classmethod
    def add_event(cls, payload, payload_type):
        # type: (Dict, str) -> None
        """
        Adds a Telemetry Request to the TelemetryWriter request buffer

        :param Dict: stores a formatted telemetry request
        """
        with cls._lock:
            if cls._instance is None:
                return

            request = create_telemetry_request(payload, payload_type, cls.sequence)
            cls.sequence += 1
            cls._instance._events_queue.append(TelemetryRequest(request, 0))

    @classmethod
    def add_integration(cls, integration_name):
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
        import pkg_resources

        payload = {
            "dependencies": [{"name": pkg.project_name, "version": pkg.version} for pkg in pkg_resources.working_set],
            "configurations": {},
        }
        cls.add_event(payload, "app-started")

    @classmethod
    def app_closed_event(cls):
        # type: () -> None
        """Adds a Telemetry request which notifies the agent that an application instance has terminated"""
        payload = {}  # type: Dict
        cls.add_event(payload, "app-closed")

    @classmethod
    def app_integrations_changed_event(cls, integrations):
        # type: (List[Dict]) -> None
        """Adds a Telemetry request which sends a list of configured integrations to the agent"""
        payload = {
            "integrations": integrations,
        }
        cls.add_event(payload, "app-integrations-changed")

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
    def enable(cls):
        # type: () -> None
        with cls._lock:
            if cls._instance is not None:
                return

            telemetry_writer = cls()
            telemetry_writer.start()

            forksafe.register(cls._restart)

            cls._instance = telemetry_writer
            cls.enabled = True

        cls.app_started_event()
