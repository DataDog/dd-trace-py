from typing import Any
from typing import Optional
from typing import Union


class NoOpTelemetryWriter(object):
    """No-op TelemetryWriter used when DD_INSTRUMENTATION_TELEMETRY_ENABLED=false.

    Keeps method-for-method parity with the native-backed TelemetryWriter so callers
    can use the singleton unconditionally, except for purely internal methods.
    """

    started = False

    def __init__(self, is_periodic: bool = True, agentless: Optional[bool] = None) -> None:
        pass

    def enable(self) -> bool:
        return False

    def app_started(self) -> None:
        # ProductManager._do_products() calls this unconditionally during bootstrap; it must be a
        # no-op here so ddtrace-run/ddtrace.auto startup does not crash when telemetry is disabled.
        pass

    def _get_shared_worker(self):
        # No native worker to consolidate; the trace exporter uses its own telemetry worker.
        return None

    def disable(self) -> None:
        pass

    def enable_agentless_client(self, enabled: bool = True) -> None:
        pass

    def add_integration(
        self,
        integration_name: str,
        patched: bool,
        auto_patched: Optional[bool] = None,
        error_msg: Optional[str] = None,
        version: str = "",
    ) -> None:
        pass

    def attach_dependency_metadata(
        self,
        package_name: str,
        cve_id: str,
        path: str,
        symbol: str,
        line: int,
    ) -> bool:
        return False

    def register_cve_metadata(self, package_name: str, cve_id: str) -> bool:
        return False

    def enable_sca_metadata(self) -> None:
        pass

    def product_activated(self, product: str, status: bool) -> None:
        pass

    def add_configuration(
        self,
        configuration_name: str,
        configuration_value: Any,
        origin: str = "unknown",
        config_id: Optional[str] = None,
    ) -> None:
        pass

    def add_configurations(self, configuration_list: list[tuple[str, str, str]]) -> None:
        pass

    def add_log(self, level, message: str, stack_trace: str = "", tags: Optional[dict[str, str]] = None) -> None:
        pass

    def add_error_log(self, msg: str, exc: Union[BaseException, tuple[Any, ...], None]) -> None:
        pass

    def add_gauge_metric(self, namespace, name: str, value: float, tags=None) -> None:
        pass

    def add_rate_metric(self, namespace, name: str, value: float, tags=None) -> None:
        pass

    def add_count_metric(self, namespace, name: str, value: int = 1, tags=None) -> None:
        pass

    def add_distribution_metric(self, namespace, name: str, value: float, tags=None) -> None:
        pass

    def _record_endpoint(self, endpoint) -> None:
        pass

    def _report_endpoints(self) -> None:
        # Replay hook for endpoints registered before the worker existed; no-op here.
        pass

    def set_payload_file_dir(self, output_dir: str) -> None:
        pass

    def set_test_session_token(self, token: Optional[str]) -> None:
        pass

    def _restart_sequence(self) -> None:
        pass

    def _fork_writer(self) -> None:
        pass

    def _report_dependencies(self) -> Optional[list[dict[str, Any]]]:
        return None

    def _subscribe_worker_changes(self, callback: Any) -> None:
        pass

    def periodic(self, force_flush: bool = False) -> None:
        pass

    def app_shutdown(self) -> None:
        pass

    def install_excepthook(self) -> None:
        pass

    def uninstall_excepthook(self) -> None:
        pass
