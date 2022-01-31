import time
from typing import Dict
from typing import List
from typing import Optional

from ...internal import forksafe
from ...settings import _config as config
from ..agent import get_connection
from ..agent import get_trace_url
from ..compat import get_connection_response
from ..compat import httplib
from ..encoding import JSONEncoderV2
from ..logger import get_logger
from ..periodic import PeriodicService
from ..runtime import get_runtime_id
from ..service import ServiceStatus
from ..utils.formats import get_env
from ..utils.time import StopWatch
from .data import get_application
from .data import get_host_info


log = get_logger(__name__)


def _get_interval_or_default():
    return float(get_env("instrumentation_telemetry", "interval_seconds", default=60))


class TelemetryWriter(PeriodicService):
    """
    Periodic service which sends Telemetry request payloads to the agent.
    Supports version one of the instrumentation telemetry api
    """

    # telemetry endpoint uses events platform v2 api
    ENDPOINT = "telemetry/proxy/api/v2/apmtelemetry"

    def __init__(self, agent_url=None):
        # type: (Optional[str]) -> None
        super(TelemetryWriter, self).__init__(interval=_get_interval_or_default())

        self._enabled = False  # type: bool
        self._agent_url = agent_url or get_trace_url()

        self._encoder = JSONEncoderV2()
        self._events_queue = []  # type: List[Dict]
        self._integrations_queue = []  # type: List[Dict]
        self._lock = forksafe.Lock()  # type: forksafe.ResetObject

        # _sequence is a counter representing the number of requests sent by the writer
        self._sequence = 1  # type: int

    def _send_request(self, request):
        # type: (Dict) -> httplib.HTTPResponse
        """Sends a telemetry request to the trace agent"""
        with StopWatch() as sw:
            try:
                conn = get_connection(self._agent_url)
                rb_json = self._encoder.encode(request)
                conn.request("POST", self.ENDPOINT, rb_json, self._create_headers(request["request_type"]))

                resp = get_connection_response(conn)
                log.debug(
                    "sent %d in %.5fs to %s/%s. response: %s",
                    len(rb_json),
                    sw.elapsed(),
                    self._agent_url,
                    self.ENDPOINT,
                    resp.status,
                )
                return resp
            finally:
                conn.close()

    def _flush_integrations_queue(self):
        # type () -> List[Dict]
        """Returns a list of all integrations queued by add_integration"""
        with self._lock:
            integrations = self._integrations_queue
            self._integrations_queue = []
        return integrations

    def _flush_events_queue(self):
        # type () -> List[Dict]
        """Returns a list of all integrations queued by classmethods"""
        with self._lock:
            requests = self._events_queue
            self._events_queue = []
        return requests

    def periodic(self):
        integrations = self._flush_integrations_queue()
        if integrations:
            self._app_integrations_changed_event(integrations)

        telemetry_requests = self._flush_events_queue()

        for telemetry_request in telemetry_requests:
            try:
                resp = self._send_request(telemetry_request)
                if resp.status >= 300:
                    log.warning(
                        "failed to send telemetry to the Datadog Agent at %s/%s. response: %s",
                        self._agent_url,
                        self.ENDPOINT,
                        resp.status,
                    )
            except Exception:
                log.warning(
                    "failed to send telemetry to the Datadog Agent at %s/%s.",
                    self._agent_url,
                    self.ENDPOINT,
                    exc_info=True,
                )

    def shutdown(self):
        # type: () -> None
        self._app_closing_event()
        self.periodic()

    def add_event(self, payload, payload_type):
        # type: (Dict, str) -> None
        """
        Adds a Telemetry Request to the TelemetryWriter request buffer

        :param Dict payload: stores a formatted telemetry request
        :param str payload_type: The payload_type denotes the type of telmetery request.
            Payload types accepted by telemetry/proxy v1: app-started, app-closing, app-integrations-change
        """
        with self._lock:
            if not self._enabled:
                return

            request = self._create_telemetry_request(payload, payload_type, self._sequence)
            self._sequence += 1
            self._events_queue.append(request)

    def add_integration(self, integration_name, auto_enabled):
        # type: (str, bool) -> None
        """
        Creates and queues the names and settings of a patched module

        :param str integration_name: name of patched module
        :param bool auto_enabled: True if module is enabled in _monkey.PATCH_MODULES
        """
        with self._lock:
            if not self._enabled:
                return

            integration = {
                "name": integration_name,
                "version": "",
                "enabled": True,
                "auto_enabled": auto_enabled,
                "compatible": "",
                "error": "",
            }
            self._integrations_queue.append(integration)

    def app_started_event(self):
        # type: () -> None
        """Sent when TelemetryWriter is enabled or forks"""
        # pkg_resources import is inlined for performance reasons
        # This import is an expensive operation
        import pkg_resources

        payload = {
            "dependencies": [{"name": pkg.project_name, "version": pkg.version} for pkg in pkg_resources.working_set],
            "configurations": [],
        }
        self.add_event(payload, "app-started")

    def _app_closing_event(self):
        # type: () -> None
        """Adds a Telemetry request which notifies the agent that an application instance has terminated"""
        payload = {}  # type: Dict
        self.add_event(payload, "app-closing")

    def _app_integrations_changed_event(self, integrations):
        # type: (List[Dict]) -> None
        """Adds a Telemetry request which sends a list of configured integrations to the agent"""
        payload = {
            "integrations": integrations,
        }
        self.add_event(payload, "app-integrations-change")

    def _create_headers(self, payload_type):
        # type: (str) -> Dict
        """Creates request headers"""
        return {
            "Content-type": "application/json",
            "DD-Telemetry-Request-Type": payload_type,
            "DD-Telemetry-API-Version": "v1",
        }

    def _create_telemetry_request(self, payload, payload_type, sequence_id):
        # type: (Dict, str, int) -> Dict
        """Initializes the required fields for a generic Telemetry Intake Request"""
        return {
            "tracer_time": int(time.time()),
            "runtime_id": get_runtime_id(),
            "api_version": "v1",
            "seq_id": sequence_id,
            "application": get_application(config.service, config.version, config.env),
            "host": get_host_info(),
            "payload": payload,
            "request_type": payload_type,
        }

    def _restart(self):
        # type: () -> None
        if self.status == ServiceStatus.RUNNING:
            self.disable()
        self.enable()

    def disable(self):
        # type: () -> None
        """
        Disable the telemetry collection service.
        Once disabled, telemetry collection can be re-enabled by calling ``enable`` again.
        """
        with self._lock:
            if self.status == ServiceStatus.STOPPED:
                return

            forksafe.unregister(self._restart)

            self.stop()
            self._enabled = False

            self.join()

    def enable(self):
        # type: () -> None
        """
        Enable the instrumentation telemetry collection service. If the service has already been
        activated before, this method does nothing. Use ``disable`` to turn off the telemetry collection service.
        """
        with self._lock:
            if self.status == ServiceStatus.RUNNING:
                return

            self.start()
            self._enabled = True

            forksafe.register(self._restart)

        # add_event _locks around adding to the events queue
        self.app_started_event()
