# -*- coding: utf-8 -*-
import http.client as httplib  # noqa: E402
import itertools
import os
import sys
import time
import traceback
from typing import TYPE_CHECKING  # noqa:F401
from typing import Any  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Set  # noqa:F401
from typing import Tuple  # noqa:F401
from typing import Union  # noqa:F401
import urllib.parse as parse

from ddtrace.internal.logger import get_logger
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
from .constants import TELEMETRY_LOG_LEVEL  # noqa:F401
from .constants import TELEMETRY_NAMESPACE
from .constants import TELEMETRY_TYPE_DISTRIBUTION
from .constants import TELEMETRY_TYPE_GENERATE_METRICS
from .constants import TELEMETRY_TYPE_LOGS
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

    def __init__(self, agentless):
        # type: (bool) -> None
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
    def url(self):
        return parse.urljoin(self._telemetry_url, self._endpoint)

    def send_event(self, request: Dict) -> Optional[httplib.HTTPResponse]:
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
                    "Instrumentation Telemetry sent %d in %.5fs to %s. response: %s",
                    len(rb_json),
                    sw.elapsed(),
                    self.url,
                    resp.status,
                )
            else:
                log.debug("Failed to send Instrumentation Telemetry to %s. response: %s", self.url, resp.status)
        except Exception as e:
            log.debug("Failed to send Instrumentation Telemetry to %s. Error: %s", self.url, str(e))
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
    _sequence = itertools.count(1)
    _ORIGINAL_EXCEPTHOOK = staticmethod(sys.excepthook)
    CWD = os.getcwd()

    def __init__(self, is_periodic=True, agentless=None):
        # type: (bool, Optional[bool]) -> None
        super(TelemetryWriter, self).__init__(interval=min(config.HEARTBEAT_INTERVAL, 10))

        # Decouples the aggregation and sending of the telemetry events
        # TelemetryWriter events will only be sent when _periodic_count == _periodic_threshold.
        # By default this will occur at 10 second intervals.
        self._periodic_threshold = int(config.HEARTBEAT_INTERVAL // self.interval) - 1
        self._periodic_count = 0
        self._is_periodic = is_periodic
        self._integrations_queue = dict()  # type: Dict[str, Dict]
        # Currently telemetry only supports reporting a single error.
        # If we'd like to report multiple errors in the future
        # we could hack it in by xor-ing error codes and concatenating strings
        self._error = (0, "")  # type: Tuple[int, str]
        self._namespace = MetricNamespace()
        self._logs = set()  # type: Set[Dict[str, Any]]
        self._forked = False  # type: bool
        self._events_queue = []  # type: List[Dict]
        self._configuration_queue = {}  # type: Dict[str, Dict]
        self._imported_dependencies: Dict[str, str] = dict()
        self._modules_already_imported: Set[str] = set()
        self._product_enablement = {product.value: False for product in TELEMETRY_APM_PRODUCT}
        self._send_product_change_updates = False
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
            if config.FORCE_START:
                self._app_started()
            # Send logged error to telemetry
            get_logger("ddtrace").addHandler(DDTelemetryErrorHandler(self))

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

        # currently self._is_periodic is always true
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

    def enable_agentless_client(self, enabled=True):
        # type: (bool) -> None

        if self._client._agentless == enabled:
            return

        self._client = _TelemetryClient(enabled)

    def _is_running(self):
        # type: () -> bool
        """Returns True when the telemetry writer worker thread is running"""
        return self._is_periodic and self._worker is not None and self.status is ServiceStatus.RUNNING

    def add_event(self, payload, payload_type):
        # type: (Union[Dict[str, Any], List[Any]], str) -> None
        """
        Adds a Telemetry event to the TelemetryWriter event buffer

        :param Dict payload: stores a formatted telemetry event
        :param str payload_type: The payload_type denotes the type of telemetry request.
            Payload types accepted by telemetry/proxy v2: app-started, app-closing, app-integrations-change
        """
        if self.enable():
            event = {
                "tracer_time": int(time.time()),
                "runtime_id": get_runtime_id(),
                "api_version": "v2",
                "seq_id": next(self._sequence),
                "debug": self._debug,
                "application": get_application(config.SERVICE, config.VERSION, config.ENV),
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

    def add_error(self, code, msg, filename, line_number):
        # type: (int, str, Optional[str], Optional[int]) -> None
        """Add an error to be submitted with an event.
        Note that this overwrites any previously set errors.
        """
        if filename and line_number is not None:
            msg = "%s:%s: %s" % (filename, line_number, msg)
        self._error = (code, msg)

    def _app_started(self, register_app_shutdown=True):
        # type: (bool) -> None
        """Sent when TelemetryWriter is enabled or forks"""
        if self._forked or self.started:
            # app-started events should only be sent by the main process
            return
        #  List of configurations to be collected

        self.started = True

        products = {
            product: {"version": tracer_version, "enabled": status}
            for product, status in self._product_enablement.items()
        }

        # SOABI should help us identify which wheels people are getting from PyPI
        self.add_configurations(get_python_config_vars())

        payload = {
            "configuration": self._flush_configuration_queue(),
            "error": {
                "code": self._error[0],
                "message": self._error[1],
            },
            "products": products,
        }  # type: Dict[str, Union[Dict[str, Any], List[Any]]]
        # Add time to value telemetry metrics for single step instrumentation
        if config.INSTALL_ID or config.INSTALL_TYPE or config.INSTALL_TIME:
            payload["install_signature"] = {
                "install_id": config.INSTALL_ID,
                "install_type": config.INSTALL_TYPE,
                "install_time": config.INSTALL_TIME,
            }

        # Reset the error after it has been reported.
        self._error = (0, "")
        self.add_event(payload, "app-started")

    def _app_heartbeat_event(self):
        # type: () -> None
        if config.DEPENDENCY_COLLECTION and time.monotonic() - self._extended_time > self._extended_heartbeat_interval:
            self._extended_time += self._extended_heartbeat_interval
            self._app_dependencies_loaded_event()
            payload = {
                "dependencies": [
                    {"name": name, "version": version} for name, version in self._imported_dependencies.items()
                ]
            }
            self.add_event(payload, "app-extended-heartbeat")
        else:
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
        with self._service_lock:
            integrations = list(self._integrations_queue.values())
            self._integrations_queue = dict()
        return integrations

    def _flush_configuration_queue(self):
        # type: () -> List[Dict]
        """Flushes and returns a list of all queued configurations"""
        with self._service_lock:
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

    def _app_dependencies_loaded_event(self):
        """Adds events to report imports done since the last periodic run"""

        if not config.DEPENDENCY_COLLECTION or not self._enabled:
            return
        with self._service_lock:
            newly_imported_deps = modules.get_newly_imported_modules(self._modules_already_imported)

        if not newly_imported_deps:
            return

        with self._service_lock:
            packages = update_imported_dependencies(self._imported_dependencies, newly_imported_deps)

        if packages:
            payload = {"dependencies": packages}
            self.add_event(payload, "app-dependencies-loaded")

    def _app_product_change(self):
        # type: () -> None
        """Adds a Telemetry event which reports the enablement of an APM product"""

        if not self._send_product_change_updates:
            return

        payload = {
            "products": {
                product: {"version": tracer_version, "enabled": status}
                for product, status in self._product_enablement.items()
            }
        }
        self.add_event(payload, "app-product-change")
        self._send_product_change_updates = False

    def product_activated(self, product, enabled):
        # type: (str, bool) -> None
        """Updates the product enablement dict"""

        if self._product_enablement.get(product, False) is enabled:
            return

        self._product_enablement[product] = enabled

        # If the app hasn't started, the product status will be included in the app_started event's payload
        if self.started:
            self._send_product_change_updates = True

    def remove_configuration(self, configuration_name):
        with self._service_lock:
            del self._configuration_queue[configuration_name]

    def add_configuration(self, configuration_name, configuration_value, origin="unknown", config_id=None):
        # type: (str, Any, str, Optional[str]) -> None
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
            self._configuration_queue[configuration_name] = config

    def add_configurations(self, configuration_list):
        """Creates and queues a list of configurations"""
        with self._service_lock:
            for name, value, _origin in configuration_list:
                self._configuration_queue[name] = {
                    "name": name,
                    "origin": _origin,
                    "value": value,
                }

    def add_log(self, level, message, stack_trace="", tags=None):
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

    def add_integration_error_log(self, msg: str, exc: BaseException) -> None:
        if config.LOG_COLLECTION_ENABLED:
            stack_trace = self._format_stack_trace(exc)
            self.add_log(
                TELEMETRY_LOG_LEVEL.ERROR,
                msg,
                stack_trace=stack_trace if stack_trace is not None else "",
            )

    def _format_stack_trace(self, exc: BaseException) -> Optional[str]:
        exc_type, exc_value, exc_traceback = type(exc), exc, exc.__traceback__
        if exc_traceback:
            tb = traceback.extract_tb(exc_traceback)
            formatted_tb = ["Traceback (most recent call last):"]
            for filename, lineno, funcname, srcline in tb:
                if self._should_redact(filename):
                    formatted_tb.append("  <REDACTED>")
                    formatted_tb.append("    <REDACTED>")
                else:
                    relative_filename = self._format_file_path(filename)
                    formatted_line = f'  File "{relative_filename}", line {lineno}, in {funcname}\n    {srcline}'
                    formatted_tb.append(formatted_line)
            if exc_type:
                formatted_tb.append(f"{exc_type.__module__}.{exc_type.__name__}: {exc_value}")
            return "\n".join(formatted_tb)

        return None

    def _should_redact(self, filename: str) -> bool:
        return "ddtrace" not in filename

    def _format_file_path(self, filename: str) -> str:
        try:
            return os.path.relpath(filename, start=self.CWD)
        except ValueError:
            return filename

    def add_gauge_metric(self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: MetricTagType = None):
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

    def add_rate_metric(self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: MetricTagType = None):
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

    def add_count_metric(self, namespace: TELEMETRY_NAMESPACE, name: str, value: int = 1, tags: MetricTagType = None):
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
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: MetricTagType = None
    ):
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

    def _flush_log_metrics(self):
        # type () -> Set[Metric]
        with self._service_lock:
            log_metrics = self._logs
            self._logs = set()
        return log_metrics

    def _generate_metrics_event(self, namespace_metrics) -> None:
        for payload_type, namespaces in namespace_metrics.items():
            for namespace, metrics in namespaces.items():
                if metrics:
                    payload = {
                        "namespace": namespace,
                        "series": metrics,
                    }
                    log.debug("%s request payload, namespace %s", payload_type, namespace)
                    if payload_type == TELEMETRY_TYPE_DISTRIBUTION:
                        self.add_event(payload, TELEMETRY_TYPE_DISTRIBUTION)
                    elif payload_type == TELEMETRY_TYPE_GENERATE_METRICS:
                        self.add_event(payload, TELEMETRY_TYPE_GENERATE_METRICS)

    def _generate_logs_event(self, logs):
        # type: (Set[Dict[str, str]]) -> None
        log.debug("%s request payload", TELEMETRY_TYPE_LOGS)
        self.add_event({"logs": list(logs)}, TELEMETRY_TYPE_LOGS)

    def _dispatch(self):
        # moved core here to avoid circular import
        from ddtrace.internal import core

        core.dispatch("telemetry.periodic")

    def periodic(self, force_flush=False, shutting_down=False):
        # ensure app_started is called at least once in case traces weren't flushed
        self._app_started()
        self._app_product_change()
        self._dispatch()

        namespace_metrics = self._namespace.flush(float(self.interval))
        if namespace_metrics:
            self._generate_metrics_event(namespace_metrics)

        logs_metrics = self._flush_log_metrics()
        if logs_metrics:
            self._generate_logs_event(logs_metrics)

        # Telemetry metrics and logs should be aggregated into payloads every time periodic is called.
        # This ensures metrics and logs are submitted in 10 second time buckets.
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

        self._app_dependencies_loaded_event()

        if shutting_down:
            self._app_closing_event()

        # Send a heartbeat event to the agent, this is required to keep RC connections alive
        self._app_heartbeat_event()

        telemetry_events = self._flush_events_queue()
        for telemetry_event in telemetry_events:
            self._client.send_event(telemetry_event)

    def app_shutdown(self):
        if self.started:
            self.periodic(force_flush=True, shutting_down=True)
        self.disable()

    def reset_queues(self):
        # type: () -> None
        self._events_queue = []
        self._integrations_queue = dict()
        self._namespace.flush()
        self._logs = set()
        self._imported_dependencies = {}
        self._configuration_queue = {}

    def _flush_events_queue(self):
        # type: () -> List[Dict]
        """Flushes and returns a list of all telemtery event"""
        with self._service_lock:
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

        if self._is_running():
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

    def _telemetry_excepthook(self, tp, value, root_traceback):
        if root_traceback is not None:
            # Get the frame which raised the exception
            traceback = root_traceback
            while traceback.tb_next:
                traceback = traceback.tb_next

            lineno = traceback.tb_frame.f_code.co_firstlineno
            filename = traceback.tb_frame.f_code.co_filename
            self.add_error(1, str(value), filename, lineno)

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

            if self._enabled and not self.started:
                self._app_started(False)

            self.app_shutdown()

        return TelemetryWriter._ORIGINAL_EXCEPTHOOK(tp, value, root_traceback)

    def install_excepthook(self):
        """Install a hook that intercepts unhandled exception and send metrics about them."""
        sys.excepthook = self._telemetry_excepthook

    def uninstall_excepthook(self):
        """Uninstall the global tracer except hook."""
        sys.excepthook = TelemetryWriter._ORIGINAL_EXCEPTHOOK
