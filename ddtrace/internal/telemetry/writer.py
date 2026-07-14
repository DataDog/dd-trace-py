# -*- coding: utf-8 -*-
import itertools
import os
import traceback
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Union

from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.logger import get_logger
from ddtrace.internal.packages import is_user_code
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._telemetry import config

from ...internal import atexit
from ...internal import excepthook
from ...internal import forksafe
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


def _convert_metric_tags(tags: Optional[MetricTagType]) -> list:
    """Convert the tag tuple form ``((k, v), ...)`` into ``["k:v", ...]``.

    Preserves the previous Cython behaviour of lowercasing the whole "k:v" token.
    """
    if not tags:
        return []
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

        application = get_application(config.SERVICE, config.VERSION, config.ENV)
        host = get_host_info()

        if self._agentless:
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

        try:
            worker = self._build_worker()
        except Exception:
            log.debug("Failed to build the native telemetry worker", exc_info=True)
            return False
        self._worker = worker

        # Every process starts its worker so it heartbeats with its own session id.
        # app-started is emitted only by the root process; this is enforced inside the
        # worker via emit_app_lifecycle (set in _build_worker), so calling start() in a
        # forked child schedules heartbeats without re-emitting app-started.
        if get_parent_runtime_id() is None and not self.started:
            self.add_configurations(get_python_config_vars())
        # Use the local (not self._worker) so mypy keeps the non-None narrowing across the
        # add_configurations() call above.
        worker.start()
        self.started = True

        return True

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
        if self._worker is not None:
            try:
                self._worker.stop(send_app_closing=get_parent_runtime_id() is None)
            except Exception:
                log.debug("Failed to stop the native telemetry worker", exc_info=True)
            self._worker = None
            self.started = False

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
            try:
                self._worker.stop(send_app_closing=False)
            except Exception:
                log.debug("Failed to stop the native telemetry worker during agentless switch", exc_info=True)
            self._worker = None
            self.started = False
            self.enable()

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

    def _report_dependencies(self) -> None:
        """Discover newly imported modules + SCA re-reports and forward them to the worker."""
        if not self._enabled or self._worker is None:
            return
        deps = self._dependency_tracker.collect_report()
        if not deps:
            return
        for dep in deps:
            name = dep["name"]
            version = dep.get("version") or None
            if "metadata" in dep:
                metadata = [(m["type"], m["value"]) for m in dep["metadata"]]
            else:
                metadata = None
            self._worker.add_dependency(name, version, metadata)

    def _report_endpoints(self) -> None:
        """Forward collected HTTP endpoints to the worker (ASM API security)."""
        from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config

        if not appsec_telemetry_config.ENDPOINT_COLLECTION_ENABLED or not self._enabled:
            return
        if self._worker is None or not endpoint_collection.endpoints:
            return

        payload = endpoint_collection.flush(appsec_telemetry_config.ENDPOINT_COLLECTION_LIMIT)
        for ep in payload.get("endpoints", []):
            self._worker.add_endpoint(
                ep.get("method", ""),
                ep.get("path", ""),
                ep.get("operation_name") or None,
                ep.get("resource_name") or None,
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

    def _add_metric_point(
        self,
        metric_type: str,
        namespace: TELEMETRY_NAMESPACE,
        name: str,
        value: float,
        tags: Optional[MetricTagType],
    ) -> None:
        if not self.enable() or self._worker is None:
            return
        enums = _native_telemetry_enums()
        self._worker.add_metric_point(
            enums["namespace"][namespace],
            str(name),  # Some callers use a class E(str, enum.Enum) for the name.
            enums["metric_type"][metric_type],
            float(value),
            _convert_metric_tags(tags),
            # ``common`` marks language-shared metrics; the previous Cython aggregator
            # reported these as common, so preserve that for backend/dashboard parity.
            common=True,
        )

    def add_gauge_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: Optional[MetricTagType] = None
    ) -> None:
        """
        Queues gauge metric
        """
        self._add_metric_point("gauge", namespace, name, value, tags)

    def add_rate_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: Optional[MetricTagType] = None
    ) -> None:
        """
        Queues rate metric
        """
        self._add_metric_point("rate", namespace, name, value, tags)

    def add_count_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: int = 1, tags: Optional[MetricTagType] = None
    ) -> None:
        """
        Queues count metric
        """
        self._add_metric_point("count", namespace, name, value, tags)

    def add_distribution_metric(
        self, namespace: TELEMETRY_NAMESPACE, name: str, value: float, tags: Optional[MetricTagType] = None
    ) -> None:
        """
        Queues distributions metric
        """
        self._add_metric_point("distribution", namespace, name, value, tags)

    def periodic(self, force_flush: bool = False, shutting_down: bool = False) -> None:
        """Discover dependencies/endpoints and force a flush of the native worker.

        The native worker manages its own heartbeat scheduling on the shared runtime;
        this method exists for API compatibility and to drive the Python-side discovery
        (dependencies, endpoints) and dispatch hook.

        Args:
            force_flush: If True, force an immediate data flush.
            shutting_down: If True, the worker app-closing is driven by ``app_shutdown``.
        """
        from ddtrace.internal import core

        if self._worker is None:
            return

        # Discover dependencies/endpoints (Python side) and forward to the worker.
        if config.DEPENDENCY_COLLECTION:
            self._report_dependencies()
        self._report_endpoints()

        core.dispatch("telemetry.periodic")

        if force_flush:
            try:
                self._worker.flush()
            except Exception:
                log.debug("Failed to flush the native telemetry worker", exc_info=True)

    def app_shutdown(self) -> None:
        if self.started:
            # Final dependency/endpoint discovery + FLUSH. force_flush=True is required:
            # the native Stop lifecycle only emits the observability batch (logs/metrics),
            # not the app-events batch (dependencies/integrations/configs/endpoints), so the
            # deps/endpoints discovered here must be flushed before stop() runs.
            self.periodic(force_flush=True, shutting_down=True)
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
        self.enable()

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
