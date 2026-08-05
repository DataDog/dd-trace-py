# -*- coding: utf-8 -*-
from enum import Enum
import itertools
import os
import traceback
from typing import TYPE_CHECKING
from typing import Any
from typing import Callable
from typing import Optional
from typing import Union

from ddtrace.internal.endpoints import HttpEndPoint
from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.logger import get_logger
from ddtrace.internal.packages import is_user_code
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._telemetry import config

from ...internal import atexit
from ...internal import excepthook
from ...internal import forksafe
from ..periodic import PeriodicService
from ..runtime import get_ancestor_runtime_id
from ..runtime import get_parent_runtime_id
from ..runtime import get_runtime_id
from ..utils.formats import get_test_session_token
from ..utils.version import version as tracer_version
from .constants import TELEMETRY_APM_PRODUCT
from .constants import TELEMETRY_LOG_LEVEL
from .constants import TELEMETRY_NAMESPACE
from .constants import MetricTagType  # noqa: F401
from .data import get_application
from .data import get_host_info
from .data import get_python_config_vars
from .dependency_tracker import DependencyTracker
from .logging import DDTelemetryErrorHandler


if TYPE_CHECKING:
    from ddtrace.internal.native import MetricContext
    from ddtrace.internal.native import TelemetryWorker


log = get_logger(__name__)

# Agentless intake endpoints (base URLs). The agent path is the trace agent URL.
AGENTLESS_ENDPOINT_DATAD0G = "https://all-http-intake.logs.datad0g.com"
AGENTLESS_ENDPOINT_EU = "https://instrumentation-telemetry-intake.datadoghq.eu"


def _agentless_endpoint_url(site: str) -> str:
    """Return the agentless intake base URL for the configured site."""
    if site == "datad0g.com":
        return AGENTLESS_ENDPOINT_DATAD0G
    elif site == "datadoghq.eu":
        return AGENTLESS_ENDPOINT_EU
    return f"https://instrumentation-telemetry-intake.{site}/"


def _config_value_to_str(value: Any) -> Optional[str]:
    """Serialize a configuration value to its telemetry wire representation.

    ``None`` (an unset config) is preserved as ``None`` so the native worker emits a JSON
    ``null`` value — distinct from a config explicitly set to the empty string ``""``.
    Booleans use lowercase ``"true"``/``"false"`` rather than Python's ``str(bool)``
    (``"True"``/``"False"``) to match the telemetry configuration format the backend and the
    other tracers use. Everything else falls back to ``str()``.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


# Used for deduplication.
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


def _convert_metric_tags(tags: tuple[tuple[str, str], ...]) -> list[str]:
    """Convert the tag tuple form ``((k, v), ...)`` into ``["k:v", ...]``.

    Callers check for tags first and use the untagged ``add_point`` when there are none, so this
    is never handed an empty/None tag set.
    """
    return [f"{k}:{v}".lower() for k, v in tags]


# Map python strings to libdatadog enums.
_NATIVE_TELEMETRY_ENUMS: Optional[dict] = None


def _native_telemetry_enums() -> dict:
    global _NATIVE_TELEMETRY_ENUMS
    if _NATIVE_TELEMETRY_ENUMS is None:
        from ddtrace.internal.native import ConfigurationOrigin
        from ddtrace.internal.native import LogLevel
        from ddtrace.internal.native import MetricNamespace
        from ddtrace.internal.native import MetricType

        _NATIVE_TELEMETRY_ENUMS = {
            "namespace": {ns: getattr(MetricNamespace, ns.value) for ns in TELEMETRY_NAMESPACE},
            "metric_type": {
                "gauge": MetricType.gauge,
                "count": MetricType.count,
                "rate": MetricType.rate,
                "distribution": MetricType.distribution,
            },
            "level": {level: getattr(LogLevel, level.value) for level in TELEMETRY_LOG_LEVEL},
            "origin": ConfigurationOrigin,
        }
    return _NATIVE_TELEMETRY_ENUMS


class _TelemetryDependencyCollector(PeriodicService):
    """Periodically discovers newly imported packages and reports them to the native worker.

    Python has no cheap "module imported" event, so dependency discovery is inherently a
    poll: ``_report_dependencies`` diffs ``sys.modules`` against what has already been
    reported. Everything else the telemetry writer sends is now change-driven (metrics,
    endpoints, configuration), so this is the only remaining reason to run a periodic
    thread. It is fork-safe (``autorestart=True``): the underlying ``PeriodicThread``
    auto-resumes in forked children, where ``_report_dependencies`` no-ops until the child
    rebuilds its worker.
    """

    def __init__(self, report: "Callable[[], None]", interval: float) -> None:
        super().__init__(interval, autorestart=True)
        self._report = report

    def periodic(self) -> None:
        self._report()


class TelemetryWriter:
    """
    Submits Instrumentation Telemetry events to the datadog agent.

    Thin shim over the native ``TelemetryWorker`` (libdatadog-backed). The worker
    runs on the shared native runtime.
    """

    # Counter representing the number of configuration events. Here we are relying on the atomicity
    # of `itertools.count()` which is a CPython implementation detail. The seq_id is passed to the
    # native worker so configuration ordering is preserved for tests.
    _sequence_configurations = itertools.count(1)
    CWD = os.getcwd()

    def __init__(self, agentless: Optional[bool] = None) -> None:
        self._dependency_tracker = DependencyTracker()

        # Debug flag that enables payload debug mode.
        self._debug = config.DEBUG

        self._enabled = config.TELEMETRY_ENABLED

        if agentless is None:
            agentless = config.AGENTLESS_MODE or config.API_KEY not in (None, "")

        if agentless and not config.API_KEY:
            log.debug("Disabling telemetry: no Datadog API key found in agentless mode")
            self._enabled = False

        self._agentless = agentless

        # The native worker, lazily built in enable() once the native runtime exists.
        self._worker: Optional["TelemetryWorker"] = None
        # Registered native metric contexts, keyed by (namespace, name, type) - deliberately NOT
        # by tags, which ride along with each point instead, so this stays bounded by the number of
        # distinct metrics. ContextKeys are worker-specific, so it is cleared on every worker
        # rebuild (see enable()).
        self._metric_contexts: dict[tuple[TELEMETRY_NAMESPACE, str, str], "MetricContext"] = {}
        # Serializes first-time metric-context registration so two threads recording the same new
        # metric can't both register it (which would create duplicate native contexts / split the
        # series). Only taken on a cache miss; the hot add path reads the cache lock-free.
        self._metric_lock = forksafe.Lock()
        # Serializes building and publishing the native worker in enable().
        self._enable_lock = forksafe.Lock()
        # Callbacks notified whenever the native worker is replaced or torn down. Handles issued by
        # a worker die with it, so anything holding one (the trace exporter, for its trace_api.*
        # health metrics) has to be handed the new one rather than keeping a stale clone.
        self._worker_subscribers: list[Callable[[Optional["TelemetryWorker"]], None]] = []
        # Fork-safe periodic that polls sys.modules for newly imported dependencies. Created
        # once in enable(); forked children inherit and auto-resume it (see the class docstring).
        self._deps_collector: Optional[PeriodicService] = None
        # Test-only: when set, the worker points at a file:// endpoint that dumps each
        # telemetry request to its own file under this directory (offline/Bazel replay).
        self._payload_file_dir: Optional[str] = None
        self._test_session_token: Optional[str] = get_test_session_token()
        self.started = False

        # Product enablement is tracked so the version can be passed alongside each change.
        self._product_versions: dict[str, str] = {product.value: tracer_version for product in TELEMETRY_APM_PRODUCT}

        if self._enabled:
            # Captures unhandled exceptions during application start up
            self.install_excepthook()
            # In order to support 3.12, we start the writer upon initialization.
            # See https://github.com/python/cpython/pull/104826.
            self.enable()
            # Shut down (flush) the telemetry writer when the application exits.
            # MUST be registered AFTER enable(): enable() creates the native runtime, which
            # registers its OWN atexit shutdown. atexit runs LIFO, so registering ours last
            # makes app_shutdown's final flush run BEFORE the runtime is torn down — otherwise
            # the closing flush (app-closing, shutdown deps/endpoints) is lost on a dead runtime.
            atexit.register(self.app_shutdown)
            # Rebuild the native worker in forked children (registered AFTER the native
            # runtime's after_fork_child hook, which get_native_runtime() registered
            # during enable(), so the shared runtime is restarted before we rebuild).
            forksafe.register(self._fork_writer)
            get_logger("ddtrace").addHandler(DDTelemetryErrorHandler(self))

    def _build_worker(self) -> "TelemetryWorker":
        """Build (or rebuild) the native TelemetryWorker from current config + host/app data."""
        # Lazy import so importing the telemetry package never hard-fails if the native
        # symbol has not been built yet.
        from ddtrace.internal.native import TelemetryWorker
        from ddtrace.internal.native_runtime import get_native_runtime
        from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config

        application = get_application(config.SERVICE, config.VERSION, config.ENV)
        host = get_host_info()

        if self._payload_file_dir is not None:
            # Payload-files mode (offline replay / Bazel): redirect telemetry to a local
            # directory via a file:// endpoint. libdatadog's dump server writes each request
            # body to its own file when the endpoint path ends in a separator. os.path.join
            # with "" guarantees that trailing separator.
            endpoint_url = "file://" + os.path.join(self._payload_file_dir, "")
            api_key = None
        elif self._agentless:
            endpoint_url = _agentless_endpoint_url(config.SITE)
            api_key = config.API_KEY
        else:
            endpoint_url = agent_config.trace_agent_url
            api_key = None

        return TelemetryWorker(
            get_native_runtime(),
            service=application["service_name"],
            env=application["env"] or None,
            app_version=application["service_version"] or None,
            language_name=application["language_name"],
            language_version=application["language_version"],
            tracer_version=application["tracer_version"],
            runtime_id=get_runtime_id(),
            runtime_name=application.get("runtime_name"),
            runtime_version=application.get("runtime_version"),
            process_tags=application.get("process_tags") or None,
            hostname=host["hostname"],
            os=host.get("os") or None,
            os_version=host.get("os_version") or None,
            architecture=host.get("architecture") or None,
            kernel_name=host.get("kernel_name") or None,
            kernel_release=host.get("kernel_release") or None,
            kernel_version=host.get("kernel_version") or None,
            container_id=host.get("container_id") or None,
            endpoint_url=endpoint_url,
            api_key=api_key,
            session_id=get_runtime_id(),
            parent_session_id=get_parent_runtime_id(),
            root_session_id=get_ancestor_runtime_id(),
            heartbeat_interval_secs=config.HEARTBEAT_INTERVAL,
            extended_heartbeat_interval_secs=config.EXTENDED_HEARTBEAT_INTERVAL,
            debug_enabled=self._debug,
            # Only the root process emits app-started/app-closing; forked children
            # heartbeat with their own session id but must not re-emit them.
            emit_app_lifecycle=get_parent_runtime_id() is None,
            endpoints_message_limit=appsec_telemetry_config.ENDPOINT_COLLECTION_LIMIT,
            test_session_token=self._test_session_token,
            # Single-step-instrumentation install metadata
            install_id=config.INSTALL_ID,
            install_type=config.INSTALL_TYPE,
            install_time=config.INSTALL_TIME,
        )

    def enable(self) -> bool:
        """
        Enable the instrumentation telemetry collection service. If the service has already been
        activated before, this method does nothing. Use ``disable`` to turn off the telemetry collection service.
        """
        if not self._enabled:
            return False

        if self._worker is not None:
            return True

        with self._enable_lock:
            # extra check to skip the self._worker check on the hotter path
            if self._worker is not None:
                return True  # type: ignore[unreachable]

            try:
                worker = self._build_worker()
            except Exception:
                log.debug("Failed to build the native telemetry worker", exc_info=True)
                return False
            self._metric_contexts.clear()
            self._worker = worker
            self._notify_worker_changed(worker)

        # Every process starts its worker so it heartbeats with its own session id.
        # app-started is emitted only by the root process; this is enforced inside the
        # worker via emit_app_lifecycle (set in _build_worker), so calling start() in a
        # forked child schedules heartbeats without re-emitting app-started.
        # The root process defers app-started until startup configuration has been reported
        # (products load + report_configuration run after enable()); see app_started(), which is
        # invoked once products are loaded. (Forked children never emit app-started, so just start.)
        if get_parent_runtime_id() is None:
            if not self.started:
                self.add_configurations(get_python_config_vars())
        else:
            worker.start()
            self.started = True

        # Subscribe before replaying, so an endpoint registered in between is forwarded twice
        # rather than lost; the native worker dedupes them (ASM API security).
        endpoint_collection.on_endpoint_registered = self._record_endpoint
        self._report_endpoints()

        # Start the telemetry periodic. Each tick runs periodic() (without a forced flush): it polls
        # for new dependencies and drives the app-started fallback, so it runs even when dependency
        # collection is disabled. Created once (root process); forked children inherit it and
        # auto-resume (autorestart=True), so only create it when it does not already exist.
        if self._deps_collector is None:
            self._deps_collector = _TelemetryDependencyCollector(self.periodic, config.HEARTBEAT_INTERVAL)
            self._deps_collector.start()

        return True

    def app_started(self) -> None:
        """Emit the root process's app-started event, exactly once."""
        if self.started:
            return
        if not self.enable() or self._worker is None:
            return
        # enable() starts the worker directly in forked children (and sets started there), so
        # re-check afterwards to avoid starting the worker twice. mypy can't see that enable()
        # mutates self.started, so it flags this guard as unreachable.
        if self.started:
            return  # type: ignore[unreachable]
        # Discover dependencies before the worker starts: the native Start action schedules the
        # first extended heartbeat, which snapshots whatever is in the store when it fires. With
        # a short extended-heartbeat interval that tick can beat the first periodic poll, sending
        # a snapshot with no dependencies at all. (mostly relevant in tests.)
        if config.DEPENDENCY_COLLECTION:
            self._report_dependencies()
        try:
            self._worker.start()
        except Exception:
            log.debug("Failed to start the native telemetry worker", exc_info=True)
            return
        self.started = True

    def _get_shared_worker(self):
        """Return the native telemetry worker for this process, so the trace exporter can
        report its ``trace_api.*`` health metrics through the same worker instead of spawning
         a second one.

        ``enable()`` is idempotent and always called to ensure existence of the worker.
        """
        self.enable()
        return self._worker

    def disable(self) -> None:
        """
        Disable the telemetry collection service and drop the existing integrations and events
        Once disabled, telemetry collection can not be re-enabled.
        """
        self._enabled = False
        if endpoint_collection.on_endpoint_registered == self._record_endpoint:
            endpoint_collection.on_endpoint_registered = None
        if self._deps_collector is not None:
            try:
                self._deps_collector.stop()
            except Exception:
                log.debug("Failed to stop the telemetry dependency collector", exc_info=True)
            self._deps_collector = None
        if self._worker is not None:
            try:
                self._worker.stop(send_app_closing=get_parent_runtime_id() is None)
            except Exception:
                log.debug("Failed to stop the native telemetry worker", exc_info=True)
            self._worker = None
            self.started = False
            self._notify_worker_changed(None)

    def _subscribe_worker_changes(self, callback: "Callable[[Optional[TelemetryWorker]], None]") -> None:
        if callback not in self._worker_subscribers:
            self._worker_subscribers.append(callback)

    def _notify_worker_changed(self, worker: Optional["TelemetryWorker"]) -> None:
        for callback in list(self._worker_subscribers):
            try:
                callback(worker)
            except Exception:
                log.debug("Telemetry worker subscriber failed", exc_info=True)

    def enable_agentless_client(self, enabled: bool = True) -> None:
        if self._agentless == enabled:
            return

        self._agentless = enabled

        if enabled and not config.API_KEY:
            log.debug("Cannot switch telemetry to agentless mode: no Datadog API key found")
            return

        # Rebuild the worker against the new endpoint/api_key. It is called early,
        # before heavy traffic.
        if self._worker is not None:
            # Make sure to restart the worker if it was already running.
            was_started = self.started
            try:
                self._worker.stop(send_app_closing=False)
            except Exception:
                log.debug("Failed to stop the native telemetry worker during agentless switch", exc_info=True)
            self._worker = None
            self.started = False
            self._notify_worker_changed(None)
            self.enable()
            if was_started:
                self.app_started()

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
        if not self.enable() or self._worker is None:
            return

        compatible = None if error_msg is None else (error_msg == "")
        self._worker.add_integration(
            integration_name,
            version or None,
            patched,
            compatible,
            auto_patched,
            # Preserve the failure detail so the backend keeps the message/stack for diagnosing
            # patch failures; empty means "compatible, no error" -> send null.
            error_msg or None,
        )

    def attach_dependency_metadata(
        self,
        package_name: str,
        cve_id: str,
        path: str,
        symbol: str,
        line: int,
    ) -> bool:
        """Attach reachability metadata to an imported dependency.

        Delegates to DependencyTracker.attach_metadata().
        """
        return self._dependency_tracker.attach_metadata(package_name, cve_id, path, symbol, line)

    def register_cve_metadata(self, package_name: str, cve_id: str) -> bool:
        """Register a CVE on a dependency with reached=[].

        Called at CVE load time. Delegates to DependencyTracker.register_cve().
        """
        return self._dependency_tracker.register_cve(package_name, cve_id)

    def enable_sca_metadata(self) -> None:
        """Activate SCA metadata on all tracked and future dependencies.

        Delegates to DependencyTracker.enable_sca_metadata().
        """
        self._dependency_tracker.enable_sca_metadata()

    def _report_dependencies(self) -> Optional[list]:
        """Discover newly imported modules + SCA re-reports and forward them to the worker.

        Returns the reported dependency records or ``None`` when nothing was reported for testing.
        """
        if not self._enabled or self._worker is None:
            return None
        deps = self._dependency_tracker.collect_report()
        if not deps:
            return None
        for dep in deps:
            name = dep["name"]
            version = dep.get("version") or None
            if "metadata" in dep:
                metadata = [(m["type"], m["value"]) for m in dep["metadata"]]
            else:
                metadata = None
            self._worker.add_dependency(name, version, metadata)
        return deps

    def _record_endpoint(self, endpoint: HttpEndPoint) -> None:
        """Forward a single newly-registered HTTP endpoint to the worker (ASM API security).

        Subscribed to ``endpoint_collection`` by ``enable()``, which also replays the endpoints
        registered before then. The native worker dedupes endpoints, so a replay overlapping with
        an eager forward cannot create duplicates.
        """
        from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config

        if not appsec_telemetry_config.ENDPOINT_COLLECTION_ENABLED or not self._enabled:
            return
        worker = self._worker
        if worker is None:
            return
        worker.add_endpoint(
            endpoint.method,
            endpoint.path,
            endpoint.operation_name or None,
            endpoint.resource_name or None,
            response_body_type=list(endpoint.response_body_type) or None,
            response_code=list(endpoint.response_code) or None,
        )

    def _report_endpoints(self) -> None:
        """Replay every collected HTTP endpoint to the worker (ASM API security).

        Called from ``enable()`` to forward endpoints registered before this writer subscribed;
        endpoints registered afterwards arrive through ``_record_endpoint``.
        """
        from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config

        if not appsec_telemetry_config.ENDPOINT_COLLECTION_ENABLED or not self._enabled:
            return
        worker = self._worker
        if worker is None:
            return
        for endpoint in endpoint_collection.endpoints:
            worker.add_endpoint(
                endpoint.method,
                endpoint.path,
                endpoint.operation_name or None,
                endpoint.resource_name or None,
                response_body_type=list(endpoint.response_body_type) or None,
                response_code=list(endpoint.response_code) or None,
            )

    def product_activated(self, product: str, status: bool) -> None:
        """Updates the product enablement state and emits an app-product-change."""
        if not self.enable() or self._worker is None:
            return
        version = self._product_versions.get(product, tracer_version)
        self._worker.add_product_change(product, status, version)

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
        elif isinstance(configuration_value, (set, frozenset)):
            configuration_value = ",".join(sorted(str(v) for v in configuration_value))
        elif isinstance(configuration_value, (list, tuple)):
            configuration_value = ",".join(str(v) for v in configuration_value)
        elif not isinstance(configuration_value, (bool, str, int, float, type(None))):
            # convert unsupported types to strings
            configuration_value = str(configuration_value)

        if not self.enable() or self._worker is None:
            return

        seq_id = next(self._sequence_configurations)
        origin_cls = _native_telemetry_enums()["origin"]
        self._worker.add_configuration(
            configuration_name,
            _config_value_to_str(configuration_value),
            getattr(origin_cls, origin, origin_cls.unknown),
            config_id,
            seq_id,
        )

    def add_configurations(self, configuration_list: list[tuple[str, str, str]]) -> None:
        """Creates and queues a list of configurations"""
        if not self.enable() or self._worker is None:
            return
        origin_cls = _native_telemetry_enums()["origin"]
        for name, value, origin in configuration_list:
            seq_id = next(self._sequence_configurations)
            self._worker.add_configuration(
                name, _config_value_to_str(value), getattr(origin_cls, origin, origin_cls.unknown), None, seq_id
            )

    def add_log(self, level, message: str, stack_trace: str = "", tags: Optional[dict] = None) -> None:
        """
        Queues log. This event is meant to send library logs to Datadog's backend through the Telemetry intake.
        This will make support cycles easier and ensure we know about potentially silent issues in libraries.
        """
        if tags is None:
            tags = {}

        if not self.enable() or self._worker is None:
            return

        tags_str = None
        if tags:
            tags_str = ",".join(["%s:%s" % (k, str(v).lower()) for k, v in tags.items()])

        data = LogData(
            {
                "message": message,
                "level": level.value,
            }
        )
        if tags_str:
            data["tags"] = tags_str
        if stack_trace:
            data["stack_trace"] = stack_trace
        identifier = hash(data) & 0xFFFFFFFFFFFFFFFF

        self._worker.add_log(
            identifier,
            message,
            _native_telemetry_enums()["level"][level],
            stack_trace or None,
            tags_str,
        )

    def add_error_log(self, msg: str, exc: Union[BaseException, tuple, None]) -> None:
        if config.LOG_COLLECTION_ENABLED:
            stack_trace = None if exc is None else self._format_stack_trace(exc)

            error_type = "unknown"
            try:
                if exc is not None:
                    if isinstance(exc, tuple) and len(exc) == 3:
                        error_type = exc[0].__name__
                    else:
                        error_type = type(exc).__name__
            except Exception:
                log.debug("Failed to extract error type from exception: %r", (exc,), exc_info=True)

            self.add_log(
                TELEMETRY_LOG_LEVEL.ERROR,
                msg,
                stack_trace=stack_trace if stack_trace is not None else "",
                tags={
                    "error_type": error_type,
                },
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
        # Only include the last 20 frames
        for filename, lineno, funcname, srcline in tb[-20:]:
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

    def _register_metric_context(
        self,
        worker: "TelemetryWorker",
        metric_type: str,
        namespace: TELEMETRY_NAMESPACE,
        name: str,
    ) -> "MetricContext":
        """Register a native metric context for a metric and cache it.

        Note: we register without tags here, otherwise with variable tags this will accumulate
        indefinitely.

        Cold path: the ``add_*_metric`` hot path calls this only on a cache miss.
        """
        # str-enum names hash/compare equal to their string value, so keying on the raw name
        # still dedupes against plain strings.
        key = (namespace, name, metric_type)
        # Double-checked locking: the hot path reached here on a cache miss, but another thread may
        # have registered the same context meanwhile. Re-check under the lock so we register (and
        # cache) each context exactly once — a duplicate registration would split the series.
        with self._metric_lock:
            context = self._metric_contexts.get(key)
            if context is not None:
                return context
            enums = _native_telemetry_enums()
            context = worker.register_metric_context(
                enums["namespace"][namespace],
                # ``name`` may be an ``E(str, enum.Enum)``, use value then, rather than str().
                name.value if isinstance(name, Enum) else str(name),
                enums["metric_type"][metric_type],
                # No tags on the context itself; they are sent per point.
                [],
                # ``common`` marks language-shared metrics
                True,
            )
            self._metric_contexts[key] = context
            return context

    # The four ``add_*_metric`` methods inline the hot path (worker fetch + cached-context lookup
    # + add_point) rather than delegating to a shared helper: metric points are recorded in tight
    # loops, so avoiding the extra Python call frame per point measurably lowers the cost.

    def add_count_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: int = 1, tags: Optional[MetricTagType] = None
    ) -> None:
        """Queues count metric"""
        # Metric recording sits in hot paths (every IAST aspect, every propagation inject), so keep
        # both branches lean. ``_worker`` is only ever set while enabled (``disable()`` clears it),
        # so the ``_enabled`` test belongs inside this branch: the enabled path stays a single
        # attribute load, while the disabled path short-circuits without paying for an ``enable()``
        # call frame on every point.
        worker = self._worker
        if worker is None:
            if not self._enabled or not self.enable():
                return
            worker = self._worker
            if worker is None:
                return
        context = self._metric_contexts.get((namespace, name, "count"))
        if context is None:
            context = self._register_metric_context(worker, "count", namespace, name)
        if tags:
            worker.add_point_with_tags(context, value, _convert_metric_tags(tags))
        else:
            worker.add_point(context, value)

    def add_gauge_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: Optional[MetricTagType] = None
    ) -> None:
        """Queues gauge metric"""
        worker = self._worker
        if worker is None:
            if not self._enabled or not self.enable():
                return
            worker = self._worker
            if worker is None:
                return
        context = self._metric_contexts.get((namespace, name, "gauge"))
        if context is None:
            context = self._register_metric_context(worker, "gauge", namespace, name)
        if tags:
            worker.add_point_with_tags(context, value, _convert_metric_tags(tags))
        else:
            worker.add_point(context, value)

    def add_rate_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: Optional[MetricTagType] = None
    ) -> None:
        """Queues rate metric"""
        worker = self._worker
        if worker is None:
            if not self._enabled or not self.enable():
                return
            worker = self._worker
            if worker is None:
                return
        context = self._metric_contexts.get((namespace, name, "rate"))
        if context is None:
            context = self._register_metric_context(worker, "rate", namespace, name)
        if tags:
            worker.add_point_with_tags(context, value, _convert_metric_tags(tags))
        else:
            worker.add_point(context, value)

    def add_distribution_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: Optional[MetricTagType] = None
    ) -> None:
        """Queues distributions metric"""
        worker = self._worker
        if worker is None:
            if not self._enabled or not self.enable():
                return
            worker = self._worker
            if worker is None:
                return
        context = self._metric_contexts.get((namespace, name, "distribution"))
        if context is None:
            context = self._register_metric_context(worker, "distribution", namespace, name)
        if tags:
            worker.add_point_with_tags(context, value, _convert_metric_tags(tags))
        else:
            worker.add_point(context, value)

    def periodic(self, force_flush: bool = False) -> None:
        """Poll for new dependencies and drive the deferred app-started; optionally flush.

        Everything but dependencies is reported to the native worker as it happens;
        only dependencies need polling, so they're reported here.

        Args:
            force_flush: If True, force an immediate data flush.
        """
        # Fallback trigger for the deferred root app-started (e.g. shutdown, CI visibility, tests
        # that flush without going through product load). No-op once already started.
        self.app_started()

        if self._worker is None:
            return

        if config.DEPENDENCY_COLLECTION:
            self._report_dependencies()

        if force_flush:
            try:
                self._worker.flush()
            except Exception:
                log.debug("Failed to flush the native telemetry worker", exc_info=True)

    def app_shutdown(self) -> None:
        if self._worker is not None:
            # Final dependency/endpoint discovery + FLUSH. force_flush=True is required:
            # the native Stop lifecycle only emits the observability batch (logs/metrics),
            # not the app-events batch (dependencies/integrations/configs/endpoints), so the
            # deps/endpoints discovered here must be flushed before stop() runs.
            self.periodic(force_flush=True)
        self.disable()

    def set_test_session_token(self, token: Optional[str]) -> None:
        """Test-only: tag telemetry with an X-Datadog-Test-Session-Token header.

        The token is baked into the native worker's endpoint, so the worker is
        rebuilt to apply it (it is set once per test, before traffic).
        """
        self._test_session_token = token or None
        if not self._enabled:
            return
        # Rebuild the worker so the new token takes effect (without the
        # non-reversible semantics of disable()).
        if self._worker is not None:
            try:
                self._worker.stop(send_app_closing=False)
            except Exception:
                log.debug("Failed to stop the native telemetry worker while setting test token", exc_info=True)
            self._worker = None
            self.started = False
            self._notify_worker_changed(None)
        self.enable()

    def set_payload_file_dir(self, output_dir: str) -> None:
        """Test-only: redirect telemetry to payload files under ``output_dir``.

        Points the native worker at a ``file://`` endpoint so libdatadog's dump server
        writes each telemetry request to its own file (offline replay / Bazel). The worker
        is rebuilt to apply the endpoint and ``started`` is reset so the next flush re-emits
        app-started with full content. app-closing is captured when the worker later stops
        (``app_shutdown``), which still points at the same file:// endpoint.
        """
        self._payload_file_dir = output_dir
        if not self._enabled:
            return
        was_started = self.started
        if self._worker is not None:
            try:
                self._worker.stop(send_app_closing=False)
            except Exception:
                log.debug("Failed to stop the native telemetry worker while enabling payload files", exc_info=True)
            self._worker = None
            self.started = False
            self._notify_worker_changed(None)
        self.enable()
        # Re-emit app-started against the file:// worker if it had already started, so the offline
        # payload directory captures the lifecycle event rather than nothing.
        if was_started:
            self.app_started()

    def _restart_sequence(self) -> None:
        # Reset the configuration seq_id counter (test determinism). The native
        # worker owns the message-batch seq_id and resets it when rebuilt.
        TelemetryWriter._sequence_configurations = itertools.count(1)

    def _fork_writer(self) -> None:
        # Runs in the child after fork. The inherited native worker was spawned with
        # restart_on_fork=False, so the shared runtime drops it (without app-closing) in the
        # child; the inherited Python handle is now inert. Drop it and rebuild lazily on the
        # child's next telemetry call (enable()), bound to the child's own runtime and session
        # ids (get_runtime_id()/get_parent_runtime_id() now reflect the child), heartbeating
        # without re-emitting app-started.
        # NOTE: rebuilding here, inside the fork-hook chain, trips a tokio IO-safety abort
        # (spawning on the just-restarted runtime from within the after-fork callbacks).
        #
        # This hook is registered before the tracer's _child_after_fork (TelemetryWriter is
        # constructed before the tracer), so it always runs before the trace-exporter rebuild
        # that calls _get_shared_worker() to get a new telemetry client.
        self._worker = None
        self.started = False
        self._notify_worker_changed(None)
        # Re-discover dependencies from scratch so the child reports its own imports.
        self._dependency_tracker.reset()

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

            self.app_shutdown()

    def install_excepthook(self) -> None:
        """Register a hook that intercepts unhandled exceptions and sends metrics about them."""
        excepthook.register(self._telemetry_excepthook)

    def uninstall_excepthook(self) -> None:
        """Unregister the telemetry excepthook."""
        excepthook.unregister(self._telemetry_excepthook)
