import itertools
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
from ..utils.formats import asbool
from ..utils.time import StopWatch
from .data import get_application
from .data import get_dependencies
from .data import get_host_info


log = get_logger(__name__)


def _get_interval_or_default():
    # type: () -> float
    return float(os.getenv("DD_TELEMETRY_HEARTBEAT_INTERVAL", default=60))


class _TelemetryClient:
    def __init__(self, endpoint):
        # type: (str) -> None
        self._agent_url = get_trace_url()
        self._endpoint = endpoint
        self._encoder = JSONEncoderV2()
        self._headers = {
            "Content-type": "application/json",
            "DD-Telemetry-API-Version": "v1",
        }

    @property
    def url(self):
        return "%s/%s" % (self._agent_url, self._endpoint)

    def send_event(self, request):
        # type: (Dict) -> Optional[httplib.HTTPResponse]
        """Sends a telemetry request to the trace agent"""
        resp = None
        try:
            rb_json = self._encoder.encode(request)
            headers = self.get_headers(request)
            with StopWatch() as sw:
                conn = get_connection(self._agent_url)
                conn.request("POST", self._endpoint, rb_json, headers)
                resp = get_connection_response(conn)
            if resp.status < 300:
                log.debug("sent %d in %.5fs to %s. response: %s", len(rb_json), sw.elapsed(), self.url, resp.status)
            else:
                log.debug("failed to send telemetry to the Datadog Agent at %s. response: %s", self.url, resp.status)
        except Exception:
            log.debug("failed to send telemetry to the Datadog Agent at %s.", self.url, exc_info=True)
        finally:
            conn.close()
        return resp

    def get_headers(self, request):
        # type: (Dict) -> Dict
        """Get all telemetry api v1 request headers"""
        headers = self._headers.copy()
        headers["DD-Telemetry-Debug-Enabled"] = request["debug"]
        headers["DD-Telemetry-Request-Type"] = request["request_type"]
        return headers


class TelemetryWriter(PeriodicService):
    """
    Submits Instrumentation Telemetry events to the datadog agent.
    Supports v1 of the instrumentation telemetry api
    """

    # telemetry endpoint uses events platform v2 api
    ENDPOINT_V1 = "telemetry/proxy/api/v2/apmtelemetry"

    def __init__(self):
        # type: () -> None
        super(TelemetryWriter, self).__init__(interval=_get_interval_or_default())

        # _enabled is None at startup, and is only set to true or false
        # after the config has been processed
        self._enabled = None  # type: Optional[bool]
        self._events_queue = []  # type: List[Dict]
        self._integrations_queue = []  # type: List[Dict]
        self._lock = forksafe.Lock()  # type: forksafe.ResetObject
        self._forked = False  # type: bool
        # Debug flag that enables payload debug mode.
        self._debug = asbool(os.environ.get("DD_TELEMETRY_DEBUG", "false"))
        forksafe.register(self._fork_writer)

        # Counter representing the number of events sent by the writer. Here we are relying on the atomicity
        # of `itertools.count()` which is a CPython implementation detail. The sequence field in telemetry
        # payloads is only used in tests and is not required to process Telemetry events.
        self._sequence = itertools.count(1)
        self._client = _TelemetryClient(self.ENDPOINT_V1)

    def _flush_integrations_queue(self):
        # type: () -> List[Dict]
        """Flushes and returns a list of all queued integrations"""
        with self._lock:
            integrations = self._integrations_queue
            self._integrations_queue = []
        return integrations

    def _flush_events_queue(self):
        # type: () -> List[Dict]
        """Flushes and returns a list of all telemtery event"""
        with self._lock:
            events = self._events_queue
            self._events_queue = []
        return events

    def reset_queues(self):
        # type: () -> None
        self._integrations_queue = []
        self._events_queue = []

    def periodic(self):
        integrations = self._flush_integrations_queue()
        if integrations:
            self._app_integrations_changed_event(integrations)

        if not self._events_queue:
            # Optimization: only queue heartbeat if no other events are queued
            self._app_heartbeat_event()

        telemetry_events = self._flush_events_queue()
        for telemetry_event in telemetry_events:
            self._client.send_event(telemetry_event)

    def _start_service(self, *args, **kwargs):
        # type: (...) -> None
        self._app_started_event()
        return super(TelemetryWriter, self)._start_service(*args, **kwargs)

    def on_shutdown(self):
        self._app_closing_event()
        self.periodic()

    def _stop_service(self, *args, **kwargs):
        # type: (...) -> None
        super(TelemetryWriter, self)._stop_service(*args, **kwargs)
        # TODO: Call this with an atexit hook
        self.join()

    def add_event(self, payload, payload_type):
        # type: (Dict, str) -> None
        """
        Adds a Telemetry event to the TelemetryWriter event buffer

        :param Dict payload: stores a formatted telemetry event
        :param str payload_type: The payload_type denotes the type of telmetery request.
            Payload types accepted by telemetry/proxy v1: app-started, app-closing, app-integrations-change
        """
        if self._enabled:
            event = {
                "tracer_time": int(time.time()),
                "runtime_id": get_runtime_id(),
                "api_version": "v1",
                "seq_id": next(self._sequence),
                "debug": self._debug,
                "application": get_application(config.service, config.version, config.env),
                "host": get_host_info(),
                "payload": payload,
                "request_type": payload_type,
            }
            self._events_queue.append(event)

    def add_integration(self, integration_name, auto_enabled):
        # type: (str, bool) -> None
        """
        Creates and queues the names and settings of a patched module

        :param str integration_name: name of patched module
        :param bool auto_enabled: True if module is enabled in _monkey.PATCH_MODULES
        """
        if self._enabled is None or self._enabled:
            # Integrations can be patched before the telemetry writer is enabled.
            integration = {
                "name": integration_name,
                "version": "",
                "enabled": True,
                "auto_enabled": auto_enabled,
                "compatible": True,
                "error": "",
            }
            self._integrations_queue.append(integration)

    def _app_started_event(self):
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

    def _app_heartbeat_event(self):
        # type: () -> None
        if self._forked:
            # TODO: Enable app-heartbeat on forks
            #   Since we only send app-started events in the main process
            #   any forked processes won't be able to access the list of
            #   dependencies for this app, and therefore app-heartbeat won't
            #   add much value today.
            return

        self.add_event({}, "app-heartbeat")

    def _app_closing_event(self):
        # type: () -> None
        """Adds a Telemetry event which notifies the agent that an application instance has terminated"""
        if self._forked:
            # app-closing event should only be sent by the main process
            return
        payload = {}  # type: Dict
        self.add_event(payload, "app-closing")

    def _app_integrations_changed_event(self, integrations):
        # type: (List[Dict]) -> None
        """Adds a Telemetry event which sends a list of configured integrations to the agent"""
        payload = {
            "integrations": integrations,
        }
        self.add_event(payload, "app-integrations-change")

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

        atexit.register(self.stop)
