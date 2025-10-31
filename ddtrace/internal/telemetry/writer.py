# -*- coding: utf-8 -*-
import http.client as httplib
import itertools
import os
import sys
import time
import traceback
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Set
from typing import Tuple
from typing import Union
import urllib.parse as parse

from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.logger import get_logger
from ddtrace.internal.packages import is_user_code
from ddtrace.internal.utils.http import get_connection
from ddtrace.settings._agent import config as agent_config
from ddtrace.settings._telemetry import config

from ...internal import atexit
from ...internal import forksafe
from ..encoding import JSONEncoderV2
from ..periodic import PeriodicService
from ..runtime import get_runtime_id
from ..service import ServiceStatus
from ..utils.time import StopWatch
from ..utils.version import version as tracer_version
from . import modules
from .constants import TELEMETRY_APM_PRODUCT
from .constants import TELEMETRY_EVENT_TYPE
from .constants import TELEMETRY_LOG_LEVEL
from .constants import TELEMETRY_NAMESPACE
from .data import get_application
from .data import get_host_info
from .data import get_python_config_vars
from .data import update_imported_dependencies
from .logging import DDTelemetryErrorHandler
from .metrics_namespaces import MetricNamespace
from .metrics_namespaces import MetricTagType
from .metrics_namespaces import MetricType


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
    AGENT_ENDPOINT = "telemetry/proxy/api/v2/apmtelemetry"
    AGENTLESS_ENDPOINT_V2 = "api/v2/apmtelemetry"

    def __init__(self, agentless: bool) -> None:
        self._telemetry_url = self.get_host(config.SITE, agentless)
        self._endpoint = self.get_endpoint(agentless)
        self._encoder = JSONEncoderV2()
        self._agentless = agentless

        self._headers = {
            "Content-Type": "application/json",
            "DD-Client-Library-Language": "python",
            "DD-Client-Library-Version": tracer_version,
        }

        if agentless and config.API_KEY:
            self._headers["dd-api-key"] = config.API_KEY

    @property
    def url(self) -> str:
        return parse.urljoin(self._telemetry_url, self._endpoint)

    def send_event(self, request: Dict, payload_type: str) -> Optional[httplib.HTTPResponse]:
        """Sends a telemetry request to the trace agent"""
        resp = None
        conn = None
        try:
            rb_json, _ = self._encoder.encode(request)
            headers = self.get_headers(request)
            with StopWatch() as sw:
                conn = get_connection(self._telemetry_url)
                conn.request("POST", self._endpoint, rb_json, headers)
                resp = conn.getresponse()
            if resp.status < 300:
                log.debug(
                    "Instrumentation Telemetry sent %d bytes in %.5fs to %s. Event(s): %s. Response: %s",
                    len(rb_json),
                    sw.elapsed(),
                    self.url,
                    payload_type,
                    resp.status,
                )
            else:
                log.debug("Failed to send Instrumentation Telemetry to %s. Response: %s", self.url, resp.status)
        except Exception as e:
            log.debug("Failed to send Instrumentation Telemetry to %s. Error: %s", self.url, str(e))
        finally:
            if conn is not None:
                conn.close()
        return resp

    def get_headers(self, request: Dict) -> Dict:
        """Get all telemetry api v2 request headers"""
        headers = self._headers.copy()
        headers["DD-Telemetry-Debug-Enabled"] = request["debug"]
        headers["DD-Telemetry-Request-Type"] = request["request_type"]
        headers["DD-Telemetry-API-Version"] = request["api_version"]
        return headers

    def get_endpoint(self, agentless: bool) -> str:
        return self.AGENTLESS_ENDPOINT_V2 if agentless else self.AGENT_ENDPOINT

    def get_host(self, site: str, agentless: bool) -> str:
        if not agentless:
            return agent_config.trace_agent_url
        elif site == "datad0g.com":
            return "https://all-http-intake.logs.datad0g.com"
        elif site == "datadoghq.eu":
            return "https://instrumentation-telemetry-intake.datadoghq.eu"
        return f"https://instrumentation-telemetry-intake.{site}/"


class TelemetryWriter(PeriodicService):
    """
    Submits Instrumentation Telemetry events to the datadog agent.
    Supports v2 of the instrumentation telemetry api
    """

    # Counter representing the number of events sent by the writer. Here we are relying on the atomicity
    # of `itertools.count()` which is a CPython implementation detail. The sequence field in telemetry
    # payloads is only used in tests and is not required to process Telemetry events.
    _sequence_payloads = itertools.count(1)
    _sequence_configurations = itertools.count(1)
    _ORIGINAL_EXCEPTHOOK = staticmethod(sys.excepthook)
    CWD = os.getcwd()

    def __init__(self, is_periodic: bool = True, agentless: Optional[bool] = None) -> None:
        super(TelemetryWriter, self).__init__(interval=min(config.HEARTBEAT_INTERVAL, 10))

        # Decouples the aggregation and sending of the telemetry events
        # TelemetryWriter events will only be sent when _periodic_count == _periodic_threshold.
        # By default this will occur at 10 second intervals.
        self._periodic_threshold = int(config.HEARTBEAT_INTERVAL // self.interval) - 1
        self._periodic_count = 0
        self._is_periodic = is_periodic
        self._integrations_queue: Dict[str, Dict] = dict()
        self._namespace = MetricNamespace()
        self._logs: Set[Dict[str, Any]] = set()
        self._forked: bool = False
        self._events_queue: List[Dict[str, Any]] = []
        self._configuration_queue: List[Dict] = []
        self._imported_dependencies: Dict[str, str] = dict()
        self._modules_already_imported: Set[str] = set()
        self._product_enablement: Dict[str, bool] = {product.value: False for product in TELEMETRY_APM_PRODUCT}
        self._previous_product_enablement: Dict[str, bool] = {}
        self._extended_time = time.monotonic()
        # The extended heartbeat interval is set to 24 hours
        self._extended_heartbeat_interval = 3600 * 24

        self.started = False

        # Debug flag that enables payload debug mode.
        self._debug = config.DEBUG

        self._enabled = config.TELEMETRY_ENABLED

        if agentless is None:
            agentless = config.AGENTLESS_MODE or config.API_KEY not in (None, "")

        if agentless and not config.API_KEY:
            log.debug("Disabling telemetry: no Datadog API key found in agentless mode")
            self._enabled = False
        self._client = _TelemetryClient(agentless)

        if self._enabled:
            # Avoids sending app-started and app-closed events in forked processes
            forksafe.register(self._fork_writer)
            # shutdown the telemetry writer when the application exits
            atexit.register(self.app_shutdown)
            # Captures unhandled exceptions during application start up
            self.install_excepthook()
            # In order to support 3.12, we start the writer upon initialization.
            # See https://github.com/python/cpython/pull/104826.
            # Telemetry events will only be sent after the `app-started` is queued.
            # This will occur when the agent writer starts.
            self.enable()
            # Force app started for unit tests
            if config.FORCE_START and (app_started := self._report_app_started()):
                self._events_queue.append(self._get_event(app_started, TELEMETRY_EVENT_TYPE.STARTED))
            get_logger("ddtrace").addHandler(DDTelemetryErrorHandler(self))

    def enable(self) -> bool:
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

        # currently self._is_periodic is always true
        self.status = ServiceStatus.RUNNING
        return True

    def _get_event(
        self, payload: Union[Dict[str, Any], List[Any]], payload_type: TELEMETRY_EVENT_TYPE
    ) -> Dict[str, Any]:
        return {"payload": payload, "request_type": payload_type.value}

    def disable(self) -> None:
        """
        Disable the telemetry collection service and drop the existing integrations and events
        Once disabled, telemetry collection can not be re-enabled.
        """
        self._enabled = False
        self.reset_queues()

    def enable_agentless_client(self, enabled: bool = True) -> None:
        if self._client._agentless == enabled:
            return

        self._client = _TelemetryClient(enabled)

    def _is_running(self) -> bool:
        """Returns True when the telemetry writer worker thread is running"""
        return self._is_periodic and self._worker is not None and self.status is ServiceStatus.RUNNING

    def add_integration(
        self,
        integration_name: str,
        patched: bool,
        auto_patched: Optional[bool] = None,
        error_msg: Optional[str] = None,
        version: str = "",
    ) -> None:
        """
        Creates and queues the names and settings of a patched module

        :param str integration_name: name of patched module
        :param bool auto_enabled: True if module is enabled in _monkey.PATCH_MODULES
        """
        if not self.enable():
            return

        # Integrations can be patched before the telemetry writer is enabled.
        with self._service_lock:
            if integration_name not in self._integrations_queue:
                self._integrations_queue[integration_name] = {"name": integration_name}

            self._integrations_queue[integration_name]["version"] = version
            self._integrations_queue[integration_name]["enabled"] = patched

            if auto_patched is not None:
                self._integrations_queue[integration_name]["auto_enabled"] = auto_patched

            if error_msg is not None:
                self._integrations_queue[integration_name]["compatible"] = error_msg == ""
                self._integrations_queue[integration_name]["error"] = error_msg

    def _report_app_started(self, register_app_shutdown: bool = True) -> Optional[Dict[str, Any]]:
        """Sent when TelemetryWriter is enabled or forks"""
        if self._forked or self.started:
            # app-started events should only be sent by the main process
            return None
        #  List of configurations to be collected

        self.started = True

        # SOABI should help us identify which wheels people are getting from PyPI
        self.add_configurations(get_python_config_vars())

        payload = {
            "configuration": self._report_configurations(),
            "products": self._report_products(),
        }
        # Add time to value telemetry metrics for single step instrumentation
        if config.INSTALL_ID or config.INSTALL_TYPE or config.INSTALL_TIME:
            payload["install_signature"] = {
                "install_id": config.INSTALL_ID,
                "install_type": config.INSTALL_TYPE,
                "install_time": config.INSTALL_TIME,
            }
        return payload

    def _report_heartbeat(self) -> Optional[Dict[str, Any]]:
        if config.DEPENDENCY_COLLECTION and time.monotonic() - self._extended_time > self._extended_heartbeat_interval:
            self._extended_time += self._extended_heartbeat_interval
            return {
                "dependencies": [
                    {"name": name, "version": version} for name, version in self._imported_dependencies.items()
                ]
            }
        return None

    def _report_integrations(self) -> List[Dict]:
        """Flushes and returns a list of all queued integrations"""
        with self._service_lock:
            integrations = list(self._integrations_queue.values())
            self._integrations_queue = dict()
        return integrations

    def _report_configurations(self) -> List[Dict]:
        """Flushes and returns a list of all queued configurations"""
        with self._service_lock:
            configurations = self._configuration_queue
            self._configuration_queue = []
        return configurations

    def _report_dependencies(self) -> Optional[List[Dict[str, Any]]]:
        """Adds events to report imports done since the last periodic run"""
        if not config.DEPENDENCY_COLLECTION or not self._enabled:
            return None

        with self._service_lock:
            newly_imported_deps = modules.get_newly_imported_modules(self._modules_already_imported)
            if not newly_imported_deps:
                return None
            return update_imported_dependencies(self._imported_dependencies, newly_imported_deps)

    def _report_endpoints(self) -> Optional[Dict[str, Any]]:
        """Adds a Telemetry event which sends the list of HTTP endpoints found at startup to the agent"""
        import ddtrace.settings.asm as asm_config_module

        if not asm_config_module.config._api_security_endpoint_collection or not self._enabled:
            return None

        if not endpoint_collection.endpoints:
            return None

        with self._service_lock:
            return endpoint_collection.flush(asm_config_module.config._api_security_endpoint_collection_limit)

    def _report_products(self) -> Dict[str, Any]:
        """Adds a Telemetry event which reports the enablement of an APM product"""
        with self._service_lock:
            products = self._product_enablement.items()
            for product, status in products:
                self._previous_product_enablement[product] = status
            self._product_enablement = {}
            return {product: {"version": tracer_version, "enabled": status} for product, status in products}

    def product_activated(self, product: str, status: bool) -> None:
        """Updates the product enablement dict"""
        with self._service_lock:
            # Only send product change event if the product status has changed
            if self._previous_product_enablement.get(product) != status:
                self._product_enablement[product] = status

    def add_configuration(
        self,
        configuration_name: str,
        configuration_value: Any,
        origin: str = "unknown",
        config_id: Optional[str] = None,
    ) -> None:
        """Creates and queues the name, origin, value of a configuration"""
        if isinstance(configuration_value, dict):
            configuration_value = ",".join(":".join((k, str(v))) for k, v in configuration_value.items())
        elif isinstance(configuration_value, (list, tuple)):
            configuration_value = ",".join(str(v) for v in configuration_value)
        elif not isinstance(configuration_value, (bool, str, int, float, type(None))):
            # convert unsupported types to strings
            configuration_value = str(configuration_value)

        config = {
            "name": configuration_name,
            "origin": origin,
            "value": configuration_value,
        }
        if config_id:
            config["config_id"] = config_id

        with self._service_lock:
            config["seq_id"] = next(self._sequence_configurations)
            self._configuration_queue.append(config)

    def add_configurations(self, configuration_list: List[Tuple[str, str, str]]) -> None:
        """Creates and queues a list of configurations"""
        with self._service_lock:
            for name, value, origin in configuration_list:
                self._configuration_queue.append(
                    {
                        "name": name,
                        "origin": origin,
                        "value": value,
                        "seq_id": next(self._sequence_configurations),
                    }
                )

    def add_log(self, level, message: str, stack_trace: str = "", tags: Optional[Dict] = None) -> None:
        """
        Queues log. This event is meant to send library logs to Datadog's backend through the Telemetry intake.
        This will make support cycles easier and ensure we know about potentially silent issues in libraries.
        """
        if tags is None:
            tags = {}

        if self.enable():
            data = LogData(
                {
                    "message": message,
                    "level": level.value,
                    "tracer_time": int(time.time()),
                }
            )
            if tags:
                data["tags"] = ",".join(["%s:%s" % (k, str(v).lower()) for k, v in tags.items()])
            if stack_trace:
                data["stack_trace"] = stack_trace
            # Logs are hashed using the message, level, tags, and stack_trace. This should prevent duplicatation.
            self._logs.add(data)

    def add_error_log(self, msg: str, exc: Union[BaseException, tuple, None]) -> None:
        if config.LOG_COLLECTION_ENABLED:
            stack_trace = None if exc is None else self._format_stack_trace(exc)

            self.add_log(
                TELEMETRY_LOG_LEVEL.ERROR,
                msg,
                stack_trace=stack_trace if stack_trace is not None else "",
            )

    def _format_stack_trace(self, exc: Union[BaseException, tuple]) -> Optional[str]:
        if isinstance(exc, tuple) and len(exc) == 3:
            exc_type, _, exc_traceback = exc
        else:
            exc_type, _, exc_traceback = type(exc), exc, getattr(exc, "__traceback__", None)

        if not exc_traceback:
            return None

        tb = traceback.extract_tb(exc_traceback)
        formatted_tb = ["Traceback (most recent call last):"]
        for filename, lineno, funcname, srcline in tb:
            if is_user_code(filename):
                formatted_tb.append("  <REDACTED>")
                formatted_tb.append("    <REDACTED>")
            else:
                relative_filename = self._format_file_path(filename)
                formatted_line = f'  File "{relative_filename}", line {lineno}, in {funcname}\n    {srcline}'
                formatted_tb.append(formatted_line)
        if exc_type:
            formatted_tb.append(f"{exc_type.__module__}.{exc_type.__name__}: <REDACTED>")
        return "\n".join(formatted_tb)

    def _format_file_path(self, filename: str) -> str:
        try:
            if "site-packages" in filename:
                return filename.split("site-packages", 1)[1].lstrip("/")
            elif "lib/python" in filename:
                return (
                    filename.split("lib/python", 1)[1].split("/", 1)[1]
                    if "/" in filename.split("lib/python", 1)[1]
                    else "python_stdlib"
                )
            return "<REDACTED>"
        except ValueError:
            return "<REDACTED>"

    def add_gauge_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: Optional[MetricTagType] = None
    ) -> None:
        """
        Queues gauge metric
        """
        if self.status == ServiceStatus.RUNNING or self.enable():
            self._namespace.add_metric(
                MetricType.GAUGE,
                namespace,
                str(name),  # Some callers use a class E(str, enum.Enum) for the name, Cython doesn't like that.
                value,
                tags,
            )

    def add_rate_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: Optional[MetricTagType] = None
    ) -> None:
        """
        Queues rate metric
        """
        if self.status == ServiceStatus.RUNNING or self.enable():
            self._namespace.add_metric(
                MetricType.RATE,
                namespace,
                str(name),  # Some callers use a class E(str, enum.Enum) for the name, Cython doesn't like that.
                value,
                tags,
            )

    def add_count_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: int = 1, tags: Optional[MetricTagType] = None
    ) -> None:
        """
        Queues count metric
        """
        if self.status == ServiceStatus.RUNNING or self.enable():
            self._namespace.add_metric(
                MetricType.COUNT,
                namespace,
                str(name),  # Some callers use a class E(str, enum.Enum) for the name, Cython doesn't like that.
                value,
                tags,
            )

    def add_distribution_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: Optional[MetricTagType] = None
    ) -> None:
        """
        Queues distributions metric
        """
        if self.status == ServiceStatus.RUNNING or self.enable():
            self._namespace.add_metric(
                MetricType.DISTRIBUTION,
                namespace,
                str(name),  # Some callers use a class E(str, enum.Enum) for the name, Cython doesn't like that.
                value,
                tags,
            )

    def _report_logs(self) -> Set[Dict[str, Any]]:
        with self._service_lock:
            logs = self._logs
            self._logs = set()
        return logs

    def _dispatch(self) -> None:
        # moved core here to avoid circular import
        from ddtrace.internal import core

        core.dispatch("telemetry.periodic")

    def periodic(self, force_flush: bool = False, shutting_down: bool = False) -> None:
        """Process and send telemetry events in batches.

        This method handles the periodic collection and sending of telemetry data with two main timing intervals:
        1. Metrics collection interval (10 seconds by default): Collects metrics and logs
        2. Heartbeat interval (60 seconds by default): Sends all collected data to the telemetry endpoint

        The method follows this flow:
        1. Collects metrics and logs that have accumulated since last collection
        2. If not at heartbeat interval and not force_flush:
           - Queues the metrics and logs for future sending
           - Returns early
        3. At heartbeat interval or force_flush:
           - Collects app status (started, product changes)
           - Collects integration changes
           - Collects configuration changes
           - Collects dependency changes
           - Collects stored events (ex: metrics and logs)
           - Sends everything as a single batched request

        Args:
            force_flush: If True, bypasses the heartbeat interval check and sends immediately
            shutting_down: If True, includes app-closing event in the batch

        Note:
            - Metrics are collected every 10 seconds to ensure accurate time-based data
            - All data is sent in a single batch every 60 seconds to minimize network overhead
            - A heartbeat event is always included to keep RC connections alive
            - Multiple event types are combined into a single message-batch request
        """
        # Collect metrics and logs that have accumulated since last batch
        events = []
        if namespace_metrics := self._namespace.flush(float(self.interval)):
            for payload_type, namespaces in namespace_metrics.items():
                for namespace, metrics in namespaces.items():
                    if metrics:
                        events.append(self._get_event({"namespace": namespace, "series": metrics}, payload_type))

        if logs := self._report_logs():
            events.append(self._get_event({"logs": list(logs)}, TELEMETRY_EVENT_TYPE.LOGS))

        # Queue metrics if not at heartbeat interval
        if self._is_periodic and not force_flush:
            if self._periodic_count < self._periodic_threshold:
                self._periodic_count += 1
                if events:
                    # list.extend() is an atomic operation in CPython, we don't need to lock the queue
                    self._events_queue.extend(events)
                return
            self._periodic_count = 0

        # At heartbeat interval, collect and send all telemetry data
        if app_started_payload := self._report_app_started():
            # app-started should be the first event in the batch
            events = [self._get_event(app_started_payload, TELEMETRY_EVENT_TYPE.STARTED)] + events

        if products := self._report_products():
            events.append(self._get_event({"products": products}, TELEMETRY_EVENT_TYPE.PRODUCT_CHANGE))

        if ints := self._report_integrations():
            events.append(self._get_event({"integrations": ints}, TELEMETRY_EVENT_TYPE.INTEGRATIONS_CHANGE))

        if endpoints := self._report_endpoints():
            events.append(self._get_event(endpoints, TELEMETRY_EVENT_TYPE.ENDPOINTS))

        if configs := self._report_configurations():
            events.append(self._get_event({"configuration": configs}, TELEMETRY_EVENT_TYPE.CLIENT_CONFIGURATION_CHANGE))

        if deps := self._report_dependencies():
            events.append(self._get_event({"dependencies": deps}, TELEMETRY_EVENT_TYPE.DEPENDENCIES_LOADED))

        if shutting_down and not self._forked:
            events.append(self._get_event({}, TELEMETRY_EVENT_TYPE.SHUTDOWN))

        # Always include a heartbeat to keep RC connections alive
        # Extended heartbeat should be queued after app-dependencies-loaded event. This
        # ensures that that imported dependencies are accurately reported.
        if heartbeat_payload := self._report_heartbeat():
            # Extended heartbeat report dependencies while regular heartbeats report empty payloads
            events.append(self._get_event(heartbeat_payload, TELEMETRY_EVENT_TYPE.EXTENDED_HEARTBEAT))
        else:
            events.append(self._get_event({}, TELEMETRY_EVENT_TYPE.HEARTBEAT))

        # Get any queued events (ie metrics and logs from previous periodic calls) and combine with current batch
        if queued_events := self._report_events():
            events.extend(queued_events)

        # Create comma-separated list of event types for logging
        payload_types = ", ".join([event["request_type"] for event in events])
        # Prepare and send the final batch
        batch_event = {
            "tracer_time": int(time.time()),
            "runtime_id": get_runtime_id(),
            "api_version": "v2",
            "seq_id": next(self._sequence_payloads),
            "debug": self._debug,
            "application": get_application(config.SERVICE, config.VERSION, config.ENV),
            "host": get_host_info(),
            "payload": events,
            "request_type": TELEMETRY_EVENT_TYPE.MESSAGE_BATCH.value,
        }
        self._dispatch()
        self._client.send_event(batch_event, payload_types)

    def app_shutdown(self) -> None:
        if self.started:
            self.periodic(force_flush=True, shutting_down=True)
        self.disable()

    def reset_queues(self) -> None:
        self._events_queue = []
        self._integrations_queue = dict()
        self._namespace.flush()
        self._logs = set()
        self._imported_dependencies = {}
        self._configuration_queue = []

    def _report_events(self) -> List[Dict]:
        """Flushes and returns a list of all telemtery event"""
        with self._service_lock:
            events = self._events_queue
            self._events_queue = []
        return events

    def _fork_writer(self) -> None:
        self._forked = True
        # Avoid sending duplicate events.
        # Queued events should be sent in the main process.
        self.reset_queues()
        if self.status == ServiceStatus.STOPPED:
            return

        if self._is_running():
            self.stop(join=False)
        # Enable writer service in child process to avoid interpreter shutdown
        # error in Python 3.12
        self.enable()

    def _restart_sequence(self) -> None:
        self._sequence_payloads = itertools.count(1)
        self._sequence_configurations = itertools.count(1)

    def _stop_service(self, join: bool = True, *args, **kwargs) -> None:
        super(TelemetryWriter, self)._stop_service(*args, **kwargs)
        if join:
            self.join(timeout=2)

    def _telemetry_excepthook(self, tp, value, root_traceback) -> None:
        if root_traceback is not None:
            # Get the frame which raised the exception
            traceback = root_traceback
            while traceback.tb_next:
                traceback = traceback.tb_next

            lineno = traceback.tb_frame.f_code.co_firstlineno
            filename = traceback.tb_frame.f_code.co_filename

            if "ddtrace/" in filename:
                self.add_error_log("Unhandled exception from ddtrace code", (tp, None, root_traceback))

            dir_parts = filename.split(os.path.sep)
            # Check if exception was raised in the  `ddtrace.contrib` package
            if "ddtrace" in dir_parts and "contrib" in dir_parts:
                ddtrace_index = dir_parts.index("ddtrace")
                contrib_index = dir_parts.index("contrib")
                # Check if the filename has the following format:
                # `../ddtrace/contrib/integration_name/..(subpath and/or file)...`
                if ddtrace_index + 1 == contrib_index and len(dir_parts) - 2 > contrib_index:
                    integration_name = dir_parts[contrib_index + 1]
                    if "internal" in dir_parts:
                        # Check if the filename has the format:
                        # `../ddtrace/contrib/internal/integration_name/..(subpath and/or file)...`
                        internal_index = dir_parts.index("internal")
                        integration_name = dir_parts[internal_index + 1]
                    self.add_count_metric(
                        TELEMETRY_NAMESPACE.TRACERS,
                        "integration_errors",
                        1,
                        (("integration_name", integration_name), ("error_type", tp.__name__)),
                    )
                    error_msg = "{}:{} {}".format(filename, lineno, str(value))
                    self.add_integration(integration_name, True, error_msg=error_msg)

            if app_started := self._report_app_started(False):
                self._events_queue.append(self._get_event(app_started, TELEMETRY_EVENT_TYPE.STARTED))

            self.app_shutdown()

        return TelemetryWriter._ORIGINAL_EXCEPTHOOK(tp, value, root_traceback)

    def install_excepthook(self) -> None:
        """Install a hook that intercepts unhandled exception and send metrics about them."""
        sys.excepthook = self._telemetry_excepthook

    def uninstall_excepthook(self) -> None:
        """Uninstall the global tracer except hook."""
        sys.excepthook = TelemetryWriter._ORIGINAL_EXCEPTHOOK
