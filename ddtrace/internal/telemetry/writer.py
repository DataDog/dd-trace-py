# -*- coding: utf-8 -*-
import itertools
import os
import sys
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union

from ...internal import atexit
from ...internal import forksafe
from ...internal.compat import parse
from ...internal.schema import SCHEMA_VERSION
from ...internal.schema import _remove_client_service_names
from ...settings import _config as config
from ...settings.dynamic_instrumentation import config as di_config
from ...settings.exception_debugging import config as ed_config
from ...settings.peer_service import _ps_config
from ...settings.profiling import config as profiling_config
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
from ..utils.version import _pep440_to_semver
from .constants import TELEMETRY_128_BIT_TRACEID_GENERATION_ENABLED
from .constants import TELEMETRY_128_BIT_TRACEID_LOGGING_ENABLED
from .constants import TELEMETRY_AGENT_HOST
from .constants import TELEMETRY_AGENT_PORT
from .constants import TELEMETRY_AGENT_URL
from .constants import TELEMETRY_ANALYTICS_ENABLED
from .constants import TELEMETRY_ASM_ENABLED
from .constants import TELEMETRY_CLIENT_IP_ENABLED
from .constants import TELEMETRY_DOGSTATSD_PORT
from .constants import TELEMETRY_DOGSTATSD_URL
from .constants import TELEMETRY_DSM_ENABLED
from .constants import TELEMETRY_DYNAMIC_INSTRUMENTATION_ENABLED
from .constants import TELEMETRY_ENABLED
from .constants import TELEMETRY_EXCEPTION_DEBUGGING_ENABLED
from .constants import TELEMETRY_LOGS_INJECTION_ENABLED
from .constants import TELEMETRY_OBFUSCATION_QUERY_STRING_PATTERN
from .constants import TELEMETRY_OTEL_ENABLED
from .constants import TELEMETRY_PARTIAL_FLUSH_ENABLED
from .constants import TELEMETRY_PARTIAL_FLUSH_MIN_SPANS
from .constants import TELEMETRY_PRIORITY_SAMPLING
from .constants import TELEMETRY_PROFILING_ENABLED
from .constants import TELEMETRY_PROPAGATION_STYLE_EXTRACT
from .constants import TELEMETRY_PROPAGATION_STYLE_INJECT
from .constants import TELEMETRY_REMOTE_CONFIGURATION_ENABLED
from .constants import TELEMETRY_REMOTE_CONFIGURATION_INTERVAL
from .constants import TELEMETRY_RUNTIMEMETRICS_ENABLED
from .constants import TELEMETRY_SERVICE_MAPPING
from .constants import TELEMETRY_SPAN_SAMPLING_RULES
from .constants import TELEMETRY_SPAN_SAMPLING_RULES_FILE
from .constants import TELEMETRY_STARTUP_LOGS_ENABLED
from .constants import TELEMETRY_TRACE_AGENT_TIMEOUT_SECONDS
from .constants import TELEMETRY_TRACE_API_VERSION
from .constants import TELEMETRY_TRACE_COMPUTE_STATS
from .constants import TELEMETRY_TRACE_DEBUG
from .constants import TELEMETRY_TRACE_HEALTH_METRICS_ENABLED
from .constants import TELEMETRY_TRACE_PEER_SERVICE_DEFAULTS_ENABLED
from .constants import TELEMETRY_TRACE_PEER_SERVICE_MAPPING
from .constants import TELEMETRY_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED
from .constants import TELEMETRY_TRACE_SAMPLING_LIMIT
from .constants import TELEMETRY_TRACE_SAMPLING_RATE
from .constants import TELEMETRY_TRACE_SAMPLING_RULES
from .constants import TELEMETRY_TRACE_SPAN_ATTRIBUTE_SCHEMA
from .constants import TELEMETRY_TRACE_WRITER_BUFFER_SIZE_BYTES
from .constants import TELEMETRY_TRACE_WRITER_INTERVAL_SECONDS
from .constants import TELEMETRY_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES
from .constants import TELEMETRY_TRACE_WRITER_REUSE_CONNECTIONS
from .constants import TELEMETRY_TRACING_ENABLED
from .constants import TELEMETRY_TYPE_DISTRIBUTION
from .constants import TELEMETRY_TYPE_GENERATE_METRICS
from .constants import TELEMETRY_TYPE_LOGS
from .data import get_application
from .data import get_dependencies
from .data import get_host_info
from .metrics import CountMetric
from .metrics import DistributionMetric
from .metrics import GaugeMetric
from .metrics import MetricTagType
from .metrics import RateMetric
from .metrics_namespaces import MetricNamespace
from .metrics_namespaces import NamespaceMetricType


log = get_logger(__name__)


class LogData(dict):
    def __hash__(self):
        return hash((self["message"], self["level"], self.get("tags"), self.get("stack_trace")))

    def __eq__(self, other):
        return (
            self["message"] == other["message"]
            and self["level"] == other["level"]
            and self.get("tags") == other.get("tags")
            and self.get("stack_trace") == other.get("stack_trace")
        )


class _TelemetryClient:
    def __init__(self, endpoint):
        # type: (str) -> None
        self._agent_url = get_trace_url()
        self._endpoint = endpoint
        self._encoder = JSONEncoderV2()
        self._headers = {
            "Content-Type": "application/json",
            "DD-Client-Library-Language": "python",
            "DD-Client-Library-Version": _pep440_to_semver(),
        }

    @property
    def url(self):
        return parse.urljoin(self._agent_url, self._endpoint)

    def send_event(self, request):
        # type: (Dict) -> Optional[httplib.HTTPResponse]
        """Sends a telemetry request to the trace agent"""
        resp = None
        conn = None
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
            log.debug("failed to send telemetry to the Datadog Agent at %s.", self.url)
        finally:
            if conn is not None:
                conn.close()
        return resp

    def get_headers(self, request):
        # type: (Dict) -> Dict
        """Get all telemetry api v2 request headers"""
        headers = self._headers.copy()
        headers["DD-Telemetry-Debug-Enabled"] = request["debug"]
        headers["DD-Telemetry-Request-Type"] = request["request_type"]
        headers["DD-Telemetry-API-Version"] = request["api_version"]
        return headers


class TelemetryWriter(PeriodicService):
    """
    Submits Instrumentation Telemetry events to the datadog agent.
    Supports v2 of the instrumentation telemetry api
    """

    # telemetry endpoint uses events platform v2 api
    ENDPOINT_V2 = "telemetry/proxy/api/v2/apmtelemetry"
    # Counter representing the number of events sent by the writer. Here we are relying on the atomicity
    # of `itertools.count()` which is a CPython implementation detail. The sequence field in telemetry
    # payloads is only used in tests and is not required to process Telemetry events.
    _sequence = itertools.count(1)

    def __init__(self, is_periodic=True):
        # type: (bool) -> None
        super(TelemetryWriter, self).__init__(interval=min(config._telemetry_heartbeat_interval, 10))
        # Decouples the aggregation and sending of the telemetry events
        # TelemetryWriter events will only be sent when _periodic_count == _periodic_threshold.
        # By default this will occur at 10 second intervals.
        self._periodic_threshold = int(config._telemetry_heartbeat_interval // self.interval) - 1
        self._periodic_count = 0
        self._is_periodic = is_periodic
        self._integrations_queue = dict()  # type: Dict[str, Dict]
        # Currently telemetry only supports reporting a single error.
        # If we'd like to report multiple errors in the future
        # we could hack it in by xor-ing error codes and concatenating strings
        self._error = (0, "")  # type: Tuple[int, str]
        self._namespace = MetricNamespace()
        self._logs = set()  # type: Set[Dict[str, Any]]
        self._enabled = config._telemetry_enabled
        self._forked = False  # type: bool
        self._events_queue = []  # type: List[Dict]
        self._configuration_queue = {}  # type: Dict[str, Dict]
        self._lock = forksafe.Lock()  # type: forksafe.ResetObject
        self.started = False
        forksafe.register(self._fork_writer)

        # Debug flag that enables payload debug mode.
        self._debug = asbool(os.environ.get("DD_TELEMETRY_DEBUG", "false"))

        self._client = _TelemetryClient(self.ENDPOINT_V2)

    def enable(self):
        # type: () -> bool
        """
        Enable the instrumentation telemetry collection service. If the service has already been
        activated before, this method does nothing. Use ``disable`` to turn off the telemetry collection service.
        """
        if not self._enabled:
            return False

        if self.status == ServiceStatus.RUNNING:
            return True

        if self._is_periodic:
            self.start()
            return True

        self.status = ServiceStatus.RUNNING
        return True

    def disable(self):
        # type: () -> None
        """
        Disable the telemetry collection service and drop the existing integrations and events
        Once disabled, telemetry collection can not be re-enabled.
        """
        self._enabled = False
        self.reset_queues()
        if self._is_periodic and self.status is ServiceStatus.RUNNING:
            self.stop()
        else:
            self.status = ServiceStatus.STOPPED

    def add_event(self, payload, payload_type):
        # type: (Union[Dict[str, Any], List[Any]], str) -> None
        """
        Adds a Telemetry event to the TelemetryWriter event buffer

        :param Dict payload: stores a formatted telemetry event
        :param str payload_type: The payload_type denotes the type of telmetery request.
            Payload types accepted by telemetry/proxy v2: app-started, app-closing, app-integrations-change
        """
        if self.enable():
            event = {
                "tracer_time": int(time.time()),
                "runtime_id": get_runtime_id(),
                "api_version": "v2",
                "seq_id": next(self._sequence),
                "debug": self._debug,
                "application": get_application(config.service, config.version, config.env),
                "host": get_host_info(),
                "payload": payload,
                "request_type": payload_type,
            }
            self._events_queue.append(event)

    def add_integration(self, integration_name, patched, auto_patched=None, error_msg=None, version=""):
        # type: (str, bool, Optional[bool], Optional[str], Optional[str]) -> None
        """
        Creates and queues the names and settings of a patched module

        :param str integration_name: name of patched module
        :param bool auto_enabled: True if module is enabled in _monkey.PATCH_MODULES
        """
        # Integrations can be patched before the telemetry writer is enabled.
        with self._lock:
            if integration_name not in self._integrations_queue:
                self._integrations_queue[integration_name] = {"name": integration_name}

            self._integrations_queue[integration_name]["version"] = version
            self._integrations_queue[integration_name]["enabled"] = patched

            if auto_patched is not None:
                self._integrations_queue[integration_name]["auto_enabled"] = auto_patched

            if error_msg is not None:
                self._integrations_queue[integration_name]["compatible"] = error_msg == ""
                self._integrations_queue[integration_name]["error"] = error_msg

    def add_error(self, code, msg, filename, line_number):
        # type: (int, str, Optional[str], Optional[int]) -> None
        """Add an error to be submitted with an event.
        Note that this overwrites any previously set errors.
        """
        if filename and line_number is not None:
            msg = "%s:%s: %s" % (filename, line_number, msg)
        self._error = (code, msg)

    def _app_started_event(self, register_app_shutdown=True):
        # type: (bool) -> None
        """Sent when TelemetryWriter is enabled or forks"""
        if self._forked or self.started:
            # app-started events should only be sent by the main process
            return
        #  List of configurations to be collected

        self.started = True
        if register_app_shutdown:
            atexit.register(self.app_shutdown)

        self.add_configurations(
            [
                (TELEMETRY_TRACING_ENABLED, config._tracing_enabled, "unknown"),
                (TELEMETRY_STARTUP_LOGS_ENABLED, config._startup_logs_enabled, "unknown"),
                (TELEMETRY_DSM_ENABLED, config._data_streams_enabled, "unknown"),
                (TELEMETRY_ASM_ENABLED, config._appsec_enabled, "unknown"),
                (TELEMETRY_PROFILING_ENABLED, profiling_config.enabled, "unknown"),
                (TELEMETRY_DYNAMIC_INSTRUMENTATION_ENABLED, di_config.enabled, "unknown"),
                (TELEMETRY_EXCEPTION_DEBUGGING_ENABLED, ed_config.enabled, "unknown"),
                (TELEMETRY_PROPAGATION_STYLE_INJECT, ",".join(config._propagation_style_inject), "unknown"),
                (TELEMETRY_PROPAGATION_STYLE_EXTRACT, ",".join(config._propagation_style_extract), "unknown"),
                ("ddtrace_bootstrapped", config._ddtrace_bootstrapped, "unknown"),
                ("ddtrace_auto_used", "ddtrace.auto" in sys.modules, "unknown"),
                (TELEMETRY_RUNTIMEMETRICS_ENABLED, config._runtime_metrics_enabled, "unknown"),
                (TELEMETRY_TRACE_DEBUG, config._debug_mode, "unknown"),
                (TELEMETRY_ENABLED, config._telemetry_enabled, "unknown"),
                (TELEMETRY_ANALYTICS_ENABLED, config.analytics_enabled, "unknown"),
                (TELEMETRY_CLIENT_IP_ENABLED, config.client_ip_header, "unknown"),
                (TELEMETRY_LOGS_INJECTION_ENABLED, config.logs_injection, "unknown"),
                (TELEMETRY_128_BIT_TRACEID_GENERATION_ENABLED, config._128_bit_trace_id_enabled, "unknown"),
                (TELEMETRY_128_BIT_TRACEID_LOGGING_ENABLED, config._128_bit_trace_id_logging_enabled, "unknown"),
                (TELEMETRY_TRACE_COMPUTE_STATS, config._trace_compute_stats, "unknown"),
                (
                    TELEMETRY_OBFUSCATION_QUERY_STRING_PATTERN,
                    config._obfuscation_query_string_pattern.pattern.decode("ascii")
                    if config._obfuscation_query_string_pattern
                    else "",
                    "unknown",
                ),
                (TELEMETRY_OTEL_ENABLED, config._otel_enabled, "unknown"),
                (TELEMETRY_TRACE_HEALTH_METRICS_ENABLED, config.health_metrics_enabled, "unknown"),
                (TELEMETRY_RUNTIMEMETRICS_ENABLED, config._runtime_metrics_enabled, "unknown"),
                (TELEMETRY_REMOTE_CONFIGURATION_ENABLED, config._remote_config_enabled, "unknown"),
                (TELEMETRY_REMOTE_CONFIGURATION_INTERVAL, config._remote_config_poll_interval, "unknown"),
                (TELEMETRY_TRACE_SAMPLING_RATE, config._trace_sample_rate, "unknown"),
                (TELEMETRY_TRACE_SAMPLING_LIMIT, config._trace_rate_limit, "unknown"),
                (TELEMETRY_SPAN_SAMPLING_RULES, config._sampling_rules, "unknown"),
                (TELEMETRY_SPAN_SAMPLING_RULES_FILE, config._sampling_rules_file, "unknown"),
                (TELEMETRY_TRACE_SAMPLING_RULES, config._trace_sampling_rules, "unknown"),
                (TELEMETRY_PRIORITY_SAMPLING, config._priority_sampling, "unknown"),
                (TELEMETRY_PARTIAL_FLUSH_ENABLED, config._partial_flush_enabled, "unknown"),
                (TELEMETRY_PARTIAL_FLUSH_MIN_SPANS, config._partial_flush_min_spans, "unknown"),
                (TELEMETRY_TRACE_SPAN_ATTRIBUTE_SCHEMA, SCHEMA_VERSION, "unknown"),
                (TELEMETRY_TRACE_REMOVE_INTEGRATION_SERVICE_NAMES_ENABLED, _remove_client_service_names, "unknown"),
                (TELEMETRY_TRACE_PEER_SERVICE_DEFAULTS_ENABLED, _ps_config.set_defaults_enabled, "unknown"),
                (TELEMETRY_TRACE_PEER_SERVICE_MAPPING, _ps_config._unparsed_peer_service_mapping, "unknown"),
                (TELEMETRY_SERVICE_MAPPING, config._unparsed_service_mapping, "unknown"),
                (TELEMETRY_TRACE_API_VERSION, config._trace_api, "unknown"),
                (TELEMETRY_TRACE_WRITER_BUFFER_SIZE_BYTES, config._trace_writer_buffer_size, "unknown"),
                (TELEMETRY_TRACE_WRITER_MAX_PAYLOAD_SIZE_BYTES, config._trace_writer_payload_size, "unknown"),
                (TELEMETRY_TRACE_WRITER_INTERVAL_SECONDS, config._trace_writer_interval_seconds, "unknown"),
                (TELEMETRY_TRACE_WRITER_REUSE_CONNECTIONS, config._trace_writer_connection_reuse, "unknown"),
                (TELEMETRY_DOGSTATSD_PORT, config._stats_agent_port, "unknown"),
                (TELEMETRY_DOGSTATSD_URL, config._stats_agent_url, "unknown"),
                (TELEMETRY_AGENT_HOST, config._trace_agent_hostname, "unknown"),
                (TELEMETRY_AGENT_PORT, config._trace_agent_port, "unknown"),
                (TELEMETRY_AGENT_URL, config._trace_agent_url, "unknown"),
                (TELEMETRY_TRACE_AGENT_TIMEOUT_SECONDS, config._agent_timeout_seconds, "unknown"),
            ]
        )

        payload = {
            "configuration": self._flush_configuration_queue(),
            "error": {
                "code": self._error[0],
                "message": self._error[1],
            },
        }  # type: Dict[str, Union[Dict[str, Any], List[Any]]]
        # Reset the error after it has been reported.
        self._error = (0, "")
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

    def _flush_integrations_queue(self):
        # type: () -> List[Dict]
        """Flushes and returns a list of all queued integrations"""
        with self._lock:
            integrations = list(self._integrations_queue.values())
            self._integrations_queue = dict()
        return integrations

    def _flush_configuration_queue(self):
        # type: () -> List[Dict]
        """Flushes and returns a list of all queued configurations"""
        with self._lock:
            configurations = list(self._configuration_queue.values())
            self._configuration_queue = {}
        return configurations

    def _app_client_configuration_changed_event(self, configurations):
        # type: (List[Dict]) -> None
        """Adds a Telemetry event which sends list of modified configurations to the agent"""
        payload = {
            "configuration": configurations,
        }
        self.add_event(payload, "app-client-configuration-change")

    def add_configuration(self, configuration_name, configuration_value, origin="unknown"):
        # type: (str, Union[bool, float, str], str) -> None
        """Creates and queues the name, origin, value of a configuration"""
        with self._lock:
            self._configuration_queue[configuration_name] = {
                "name": configuration_name,
                "origin": origin,
                "value": configuration_value,
            }

    def add_configurations(self, configuration_list):
        # type: (List[Tuple[str, Union[bool, float, str], str]]) -> None
        """Creates and queues a list of configurations"""
        with self._lock:
            for name, value, origin in configuration_list:
                self._configuration_queue[name] = {
                    "name": name,
                    "origin": origin,
                    "value": value,
                }

    def _app_dependencies_loaded_event(self):
        # type: () -> None
        """Adds a Telemetry event which sends a list of installed python packages to the agent"""
        payload = {"dependencies": get_dependencies()}
        self.add_event(payload, "app-dependencies-loaded")

    def add_log(self, level, message, stack_trace="", tags=None):
        # type: (str, str, str, Optional[Dict]) -> None
        """
        Queues log. This event is meant to send library logs to Datadog’s backend through the Telemetry intake.
        This will make support cycles easier and ensure we know about potentially silent issues in libraries.
        """
        if tags is None:
            tags = {}

        if self.enable():
            data = LogData(
                {
                    "message": message,
                    "level": level,
                    "tracer_time": int(time.time()),
                }
            )
            if tags:
                data["tags"] = ",".join(["%s:%s" % (k, str(v).lower()) for k, v in tags.items()])
            if stack_trace:
                data["stack_trace"] = stack_trace
            self._logs.add(data)

    def add_gauge_metric(self, namespace, name, value, tags=None):
        # type: (str,str, float, MetricTagType) -> None
        """
        Queues gauge metric
        """
        if self.status == ServiceStatus.RUNNING or self.enable():
            self._namespace.add_metric(
                GaugeMetric,
                namespace,
                name,
                value,
                tags,
                self.interval,
            )

    def add_rate_metric(self, namespace, name, value=1.0, tags=None):
        # type: (str,str, float, MetricTagType) -> None
        """
        Queues rate metric
        """
        if self.status == ServiceStatus.RUNNING or self.enable():
            self._namespace.add_metric(
                RateMetric,
                namespace,
                name,
                value,
                tags,
                self.interval,
            )

    def add_count_metric(self, namespace, name, value=1.0, tags=None):
        # type: (str,str, float, MetricTagType) -> None
        """
        Queues count metric
        """
        if self.status == ServiceStatus.RUNNING or self.enable():
            self._namespace.add_metric(
                CountMetric,
                namespace,
                name,
                value,
                tags,
            )

    def add_distribution_metric(self, namespace, name, value=1.0, tags=None):
        # type: (str,str, float, MetricTagType) -> None
        """
        Queues distributions metric
        """
        if self.status == ServiceStatus.RUNNING or self.enable():
            self._namespace.add_metric(
                DistributionMetric,
                namespace,
                name,
                value,
                tags,
            )

    def _flush_log_metrics(self):
        # type () -> Set[Metric]
        with self._lock:
            log_metrics = self._logs
            self._logs = set()
        return log_metrics

    def _generate_metrics_event(self, namespace_metrics):
        # type: (NamespaceMetricType) -> None
        for payload_type, namespaces in namespace_metrics.items():
            for namespace, metrics in namespaces.items():
                if metrics:
                    payload = {
                        "namespace": namespace,
                        "series": [m.to_dict() for m in metrics.values()],
                    }
                    log.debug("%s request payload, namespace %s", payload_type, namespace)
                    if payload_type == TELEMETRY_TYPE_DISTRIBUTION:
                        self.add_event(payload, TELEMETRY_TYPE_DISTRIBUTION)
                    elif payload_type == TELEMETRY_TYPE_GENERATE_METRICS:
                        self.add_event(payload, TELEMETRY_TYPE_GENERATE_METRICS)

    def _generate_logs_event(self, payload):
        # type: (Set[Dict[str, str]]) -> None
        log.debug("%s request payload", TELEMETRY_TYPE_LOGS)
        self.add_event(list(payload), TELEMETRY_TYPE_LOGS)

    def periodic(self, force_flush=False):
        namespace_metrics = self._namespace.flush()
        if namespace_metrics:
            self._generate_metrics_event(namespace_metrics)

        logs_metrics = self._flush_log_metrics()
        if logs_metrics:
            self._generate_logs_event(logs_metrics)

        # Telemetry metrics and logs should be aggregated into payloads every time periodic is called.
        # This ensures metrics and logs are submitted in 0 to 10 second time buckets.
        # Optimization: All other events should be aggregated using `config._telemetry_heartbeat_interval`.
        # Telemetry payloads will be submitted according to `config._telemetry_heartbeat_interval`.
        if self._is_periodic and force_flush is False:
            if self._periodic_count < self._periodic_threshold:
                self._periodic_count += 1
                return
            self._periodic_count = 0

        integrations = self._flush_integrations_queue()
        if integrations:
            self._app_integrations_changed_event(integrations)

        configurations = self._flush_configuration_queue()
        if configurations:
            self._app_client_configuration_changed_event(configurations)

        if not self._events_queue:
            # Optimization: only queue heartbeat if no other events are queued
            self._app_heartbeat_event()

        telemetry_events = self._flush_events_queue()
        for telemetry_event in telemetry_events:
            self._client.send_event(telemetry_event)

    def app_shutdown(self):
        self._app_closing_event()
        self.periodic(force_flush=True)
        self.disable()

    def reset_queues(self):
        # type: () -> None
        self._events_queue = []
        self._integrations_queue = dict()
        self._namespace.flush()
        self._logs = set()

    def _flush_events_queue(self):
        # type: () -> List[Dict]
        """Flushes and returns a list of all telemtery event"""
        with self._lock:
            events = self._events_queue
            self._events_queue = []
        return events

    def _fork_writer(self):
        # type: () -> None
        self._forked = True
        # Avoid sending duplicate events.
        # Queued events should be sent in the main process.
        self.reset_queues()
        if self.status == ServiceStatus.STOPPED:
            return

        if self._is_periodic:
            self.stop(join=False)

        # Enable writer service in child process to avoid interpreter shutdown
        # error in Python 3.12
        self.enable()

    def _restart_sequence(self):
        self._sequence = itertools.count(1)

    def _stop_service(self, join=True, *args, **kwargs):
        # type: (...) -> None
        super(TelemetryWriter, self)._stop_service(*args, **kwargs)
        if join:
            self.join(timeout=2)
