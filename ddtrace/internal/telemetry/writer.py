import os
import time
from typing import Dict
from typing import List
from typing import Optional

from ...internal import atexit
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
from ..utils.formats import parse_tags_str
from ..utils.time import StopWatch
from .data import get_application
from .data import get_dependencies
from .data import get_host_info


log = get_logger(__name__)


def _get_interval_or_default():
    return float(os.getenv("DD_INSTRUMENTATION_TELEMETRY_INTERVAL_SECONDS", default=60))


class TelemetryWriter(PeriodicService):
    """
    Periodic service which sends Telemetry request payloads to the agent.
    Supports version one of the instrumentation telemetry api
    """

    # telemetry endpoint uses events platform v2 api
    ENDPOINT = "telemetry/proxy/api/v2/apmtelemetry"
    HEARTBEAT_MIN_INTERVAL = 60  # type: int

    def __init__(self, agent_url=None):
        # type: (Optional[str]) -> None
        super(TelemetryWriter, self).__init__(interval=_get_interval_or_default())

        # _enabled is None at startup, and is only set to true or false
        # after the config has been processed
        self._enabled = None  # type: Optional[bool]
        self._agent_url = agent_url or get_trace_url()

        self._encoder = JSONEncoderV2()
        self._events_queue = []  # type: List[Dict]
        self._integrations_queue = []  # type: List[Dict]
        self._lock = forksafe.Lock()  # type: forksafe.ResetObject
        self._forked = False  # type: bool
        # Set initial heartbeat time to now since we'll be sending an app-started event
        self._last_heartbeat = time.time()  # type: float

        self._headers = {
            "Content-type": "application/json",
            "DD-Telemetry-API-Version": "v1",
        }  # type: Dict[str, str]
        additional_header_str = os.environ.get("_DD_TELEMETRY_WRITER_ADDITIONAL_HEADERS")
        if additional_header_str is not None:
            self._headers.update(parse_tags_str(additional_header_str))

        # _sequence is a counter representing the number of requests sent by the writer
        self._sequence = 1  # type: int

    @property
    def url(self):
        return "%s/%s" % (self._agent_url, self.ENDPOINT)

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
        # type: () -> List[Dict]
        """Returns a list of all integrations queued by add_integration"""
        with self._lock:
            integrations = self._integrations_queue
            self._integrations_queue = []
        return integrations

    def _flush_events_queue(self):
        # type: () -> List[Dict]
        """Returns a list of all integrations queued by classmethods"""
        with self._lock:
            requests = self._events_queue
            self._events_queue = []
        return requests

    def reset_queues(self):
        # type: () -> None
        with self._lock:
            self._integrations_queue = []
            self._events_queue = []

    def periodic(self):
        integrations = self._flush_integrations_queue()
        if integrations:
            self._app_integrations_changed_event(integrations)

        if not self._events_queue:
            # Optimization: only queue heartbeat if no other events are queued
            self.app_heartbeat_event()

        telemetry_requests = self._flush_events_queue()

        for telemetry_request in telemetry_requests:
            try:
                resp = self._send_request(telemetry_request)
                if resp.status >= 300:
                    log.debug(
                        "failed to send telemetry to the Datadog Agent at %s/%s. response: %s",
                        self._agent_url,
                        self.ENDPOINT,
                        resp.status,
                    )
            except Exception:
                log.debug(
                    "failed to send telemetry to the Datadog Agent at %s/%s.",
                    self._agent_url,
                    self.ENDPOINT,
                    exc_info=True,
                )

    def _start_service(self, *args, **kwargs):
        # type: (...) -> None
        self.app_started_event()
        return super(TelemetryWriter, self)._start_service(*args, **kwargs)

    def on_shutdown(self):
        self._app_closing_event()
        self.periodic()

    def _stop_service(self, *args, **kwargs):
        # type: (...) -> None
        super(TelemetryWriter, self)._stop_service(*args, **kwargs)
        self.join()

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
            if self._enabled is not None and not self._enabled:
                return

            integration = {
                "name": integration_name,
                "version": "",
                "enabled": True,
                "auto_enabled": auto_enabled,
                "compatible": True,
                "error": "",
            }
            self._integrations_queue.append(integration)

    def app_started_event(self):
        # type: () -> None
        """Sent when TelemetryWriter is enabled or forks"""
        if self._forked:
            # app-started events should only be sent by the main process
            return
        payload = {
            "dependencies": get_dependencies(),
            "integrations": self._flush_integrations_queue(),
            "configurations": [],
        }
        self.add_event(payload, "app-started")

    def app_heartbeat_event(self):
        # type: () -> None
        if self._forked:
            # TODO: Enable app-heartbeat on forks
            #   Since we only send app-started events in the main process
            #   any forked processes won't be able to access the list of
            #   dependencies for this app, and therefore app-heartbeat won't
            #   add much value today.
            return

        # DEV: Although the default flush interval is 60 seconds,
        #   we want to explicitly control the flow here in case
        #   the flush interval ever changes.
        #
        #   If the flush interval ever exceeds 60 seconds, then we
        #   will queue a heartbeat event every flush:
        #   60 <= X <= flush interval.
        now = time.time()
        if now - self._last_heartbeat >= TelemetryWriter.HEARTBEAT_MIN_INTERVAL:
            self.add_event({}, "app-heartbeat")
            self._last_heartbeat = now

    def _app_closing_event(self):
        # type: () -> None
        """Adds a Telemetry request which notifies the agent that an application instance has terminated"""
        if self._forked:
            # app-closing event should only be sent by the main process
            return
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
        headers = self._headers.copy()
        headers["DD-Telemetry-Request-Type"] = payload_type
        return headers

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

    def _fork_writer(self):
        # type: () -> None
        self._forked = True
        # Avoid sending duplicate events.
        # Queued events should be sent in the main process.
        self.reset_queues()

    def disable(self):
        # type: () -> None
        """
        Disable the telemetry collection service and drop the existing integrations and events
        Once disabled, telemetry collection can be re-enabled by calling ``enable`` again.
        """
        with self._lock:
            self._enabled = False
        self.reset_queues()
        if self.status == ServiceStatus.STOPPED:
            return

        forksafe.unregister(self._fork_writer)
        atexit.unregister(self.stop)

        self.stop()

    def enable(self):
        # type: () -> None
        """
        Enable the instrumentation telemetry collection service. If the service has already been
        activated before, this method does nothing. Use ``disable`` to turn off the telemetry collection service.
        """
        if self.status == ServiceStatus.RUNNING:
            return

        self._enabled = True
        self.start()

        forksafe.register(self._fork_writer)
        atexit.register(self.stop)
