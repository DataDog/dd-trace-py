# For a reference on available telemetry metrics, see:
# https://github.com/DataDog/dd-go/blob/prod/trace/apps/tracer-telemetry-intake/telemetry-metrics/static/common_metrics.json

from __future__ import annotations

import dataclasses
from enum import Enum
import logging
import typing as t

from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.test_data import ITRSkippingLevel
from ddtrace.testing.internal.test_data import TestRun


if t.TYPE_CHECKING:
    from ddtrace.testing.internal.http import BackendConnectorSetup


log = logging.getLogger(__name__)

CIVISIBILITY = TELEMETRY_NAMESPACE.CIVISIBILITY


class ErrorType(str, Enum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    CODE_4XX = "status_code_4xx_response"
    CODE_5XX = "status_code_5xx_response"
    BAD_JSON = "bad_json"
    UNKNOWN = "unknown"


class EventType(str, Enum):
    TEST = "test"
    SUITE = "suite"
    MODULE = "module"
    SESSION = "session"


class GitTelemetry(str, Enum):
    GET_REPOSITORY = "get_repository"
    GET_BRANCH = "get_branch"
    CHECK_SHALLOW = "check_shallow"
    UNSHALLOW = "unshallow"
    GET_LOCAL_COMMITS = "get_local_commits"
    GET_OBJECTS = "get_objects"
    PACK_OBJECTS = "pack_objects"


class TelemetryAPI:
    _instance: t.Optional[TelemetryAPI] = None

    def __init__(self, connector_setup: BackendConnectorSetup) -> None:
        # DEV: In a beautiful world, this would set up a backend connector to the telemetry endpoint.
        # Currently we rely on ddtrace's telemetry infrastructure, so we don't have to do anything here.

        # DEV: Currently we rely on ddtrace/internal/telemetry/__init__.py setting `telemetry_writer` to be a
        # NoOpTelemetryWriter if the `DD_INSTRUMENTATION_TELEMETRY_ENABLED` environment variable is set to false.
        # If we ever start having our own telemetry writer, we will have to handle this env var ourselves.

        self.writer = telemetry_writer
        self.namespace = TELEMETRY_NAMESPACE.CIVISIBILITY
        TelemetryAPI._instance = self

    @classmethod
    def get(cls) -> TelemetryAPI:
        if not cls._instance:
            raise RuntimeError("Telemetry API called before being initialized; this is a bug")
        return cls._instance

    def with_request_metric_names(
        self, count: str, duration: str, response_bytes: t.Optional[str], error: str
    ) -> TelemetryAPIRequestMetrics:
        return TelemetryAPIRequestMetrics(
            telemetry_api=self, count=count, duration=duration, response_bytes=response_bytes, error=error
        )

    def finish(self) -> None:
        self.writer.periodic(force_flush=True)

    def add_count_metric(self, metric_name: str, value: int, tags: t.Optional[t.Dict[str, t.Any]] = None) -> None:
        log.debug("Recording Test Optimization telemetry count: %r %r %r", metric_name, value, tags)
        self.writer.add_count_metric(self.namespace, metric_name, value, self._make_tags(tags))

    def add_distribution_metric(
        self, metric_name: str, value: float, tags: t.Optional[t.Dict[str, t.Any]] = None
    ) -> None:
        log.debug("Recording Test Optimization telemetry distribution: %r %r %r", metric_name, value, tags)
        self.writer.add_distribution_metric(self.namespace, metric_name, value, self._make_tags(tags))

    def _make_tags(self, tags: t.Optional[t.Dict[str, t.Any]]) -> t.Tuple[t.Tuple[str, str], ...]:
        """
        Convert a tag dictionary into a tag tuple.

        The boolean tag value `true` is converted to the string "true". Boolean `false` as well as `None` are omitted
        from the final result. Enum items are converted to their values. Everything else is converted to string.
        """
        if not tags:
            return ()

        tag_list: t.List[t.Tuple[str, str]] = []
        for key, value in tags.items():
            if value is None or value is False:
                continue
            if value is True:
                string_value = "true"
            elif isinstance(value, Enum):
                string_value = str(value.value)
            else:
                string_value = str(value)
            tag_list.append((key, string_value))

        return tuple(tag_list)

    # Coverage.

    def record_coverage_started(self, test_framework: str, coverage_library: str) -> None:
        tags = {"library": coverage_library, "test_framework": test_framework}
        self.add_count_metric("code_coverage_started", 1, tags)

    def record_coverage_finished(self, test_framework: str, coverage_library: str) -> None:
        tags = {"library": coverage_library, "test_framework": test_framework}
        self.add_count_metric("code_coverage_finished", 1, tags)

    def record_coverage_is_empty(self) -> None:
        self.add_count_metric("code_coverage.is_empty", 1)

    def record_coverage_files(self, count_files: int) -> None:
        self.add_distribution_metric("code_coverage.files", count_files)

    # API calls.

    def record_known_tests_count(self, count: int) -> None:
        self.add_distribution_metric("known_tests.response_tests", count)

    def record_skippable_count(self, count: int, level: ITRSkippingLevel) -> None:
        skippable_count_metric = (
            "itr_skippable_tests.response_suites"
            if level == ITRSkippingLevel.SUITE
            else "itr_skippable_tests.response_tests"
        )
        self.add_count_metric(skippable_count_metric, count)

    def record_settings(self, settings: Settings) -> None:
        tags = {
            "coverage_enabled": settings.coverage_enabled,
            "itrskip_enabled": settings.skipping_enabled,
            "require_git": settings.require_git,
            "itr_enabled": settings.itr_enabled,
            "known_tests_enabled": settings.known_tests_enabled,
            "flaky_test_retries_enabled": settings.auto_test_retries.enabled,
            "early_flake_detection_enabled": settings.early_flake_detection.enabled,
            "test_management_enabled": settings.test_management.enabled,
        }

        self.add_count_metric("git_requests.settings_response", 1, tags)

    def record_test_management_tests_count(self, count: int) -> None:
        self.add_distribution_metric("test_management_tests.response_tests", count)

    def record_git_command(self, command: GitTelemetry, elapsed_seconds: float, exit_code: int) -> None:
        tags = {"command": command.value}
        self.add_count_metric("git.command", 1, tags)
        self.add_distribution_metric("git.command_ms", elapsed_seconds * 1000, tags)

        if exit_code:
            self.add_count_metric("git.command_errors", 1, {"command": command.value, "exit_code": str(exit_code)})

    # Event payloads sent by writers.

    def record_event_payload(
        self,
        endpoint: str,
        payload_size: int,
        request_seconds: float,
        events_count: int,
        error: t.Optional[ErrorType],
    ) -> None:
        tags = {"endpoint": endpoint}

        self.add_distribution_metric("endpoint_payload.bytes", payload_size, tags)
        self.add_count_metric("endpoint_payload.requests", 1, tags)
        self.add_distribution_metric("endpoint_payload.requests_ms", request_seconds * 1000, tags)
        self.add_distribution_metric("endpoint_payload.events_count", events_count, tags)

        if error:
            self.record_event_payload_error(endpoint, error)

    def record_event_payload_serialization_seconds(self, endpoint: str, serialization_seconds: float) -> None:
        tags = {"endpoint": endpoint}
        self.add_distribution_metric("endpoint_payload.events_serialization_ms", serialization_seconds * 1000, tags)

    def record_event_payload_error(self, endpoint: str, error: ErrorType) -> None:
        # `endpoint_payload.requests_errors` accepts a different set of error types, so we need to convert them here.
        if error == ErrorType.TIMEOUT:
            endpoint_error = "timeout"
        elif error in (ErrorType.CODE_4XX, ErrorType.CODE_5XX):
            endpoint_error = "status_code"
        else:
            endpoint_error = "network"

        tags = {"endpoint": endpoint, "error_type": endpoint_error}
        self.add_count_metric("endpoint_payload.requests_errors", 1, tags)

    # Test creation/finish events.

    def record_test_created(self, test_framework: str, test_run: TestRun) -> None:
        tags = {
            "event_type": EventType.TEST.value,
            "test_framework": test_framework,
            "is_benchmark": test_run.is_benchmark(),
        }
        self.add_count_metric("event_created", 1, tags)

    def record_test_finished(
        self, test_framework: str, test_run: TestRun, ci_provider_name: t.Optional[str], is_auto_injected: bool
    ) -> None:
        tags = {
            "event_type": EventType.TEST.value,
            "test_framework": test_framework,
            "is_benchmark": test_run.is_benchmark(),
            "is_new": test_run.test.is_new(),
            "is_retry": test_run.is_retry(),
            "is_rum": test_run.is_rum(),
            "browser_driver": test_run.get_browser_driver(),
            "early_flake_detection_abort_reason": test_run.test.get_early_flake_detection_abort_reason(),
            "is_quarantined": test_run.test.is_quarantined(),
            "is_disabled": test_run.test.is_disabled(),
            "is_attempt_to_fix": test_run.test.is_attempt_to_fix(),
            "has_failed_all_retries": test_run.has_failed_all_retries(),
            "provider_name": ci_provider_name,
            "auto_injected": is_auto_injected,
        }

        self.add_count_metric("event_finished", 1, tags)

    def record_suite_created(self, test_framework: str) -> None:
        tags = {"event_type": EventType.SUITE.value, "test_framework": test_framework}
        self.add_count_metric("event_created", 1, tags)

    def record_suite_finished(self, test_framework: str) -> None:
        tags = {"event_type": EventType.SUITE.value, "test_framework": test_framework}
        self.add_count_metric("event_finished", 1, tags)

    def record_module_created(self, test_framework: str) -> None:
        tags = {"event_type": EventType.MODULE.value, "test_framework": test_framework}
        self.add_count_metric("event_created", 1, tags)

    def record_module_finished(self, test_framework: str) -> None:
        tags = {"event_type": EventType.MODULE.value, "test_framework": test_framework}
        self.add_count_metric("event_finished", 1, tags)

    def record_session_created(self, test_framework: str, has_codeowners: bool, is_unsupported_ci: bool) -> None:
        tags = {
            "event_type": EventType.SESSION.value,
            "test_framework": test_framework,
            "has_codeowners": has_codeowners,
            "is_unsupported_ci": is_unsupported_ci,
        }

        self.add_count_metric("event_created", 1, tags)

    def record_session_finished(
        self, test_framework: str, has_codeowners: bool, is_unsupported_ci: bool, efd_abort_reason: t.Optional[str]
    ) -> None:
        tags = {
            "event_type": EventType.SESSION.value,
            "test_framework": test_framework,
            "has_codeowners": has_codeowners,
            "is_unsupported_ci": is_unsupported_ci,
            "early_flake_detection_abort_reason": efd_abort_reason,
        }

        self.add_count_metric("event_finished", 1, tags)

    def record_git_pack_data(self, uploaded_files: int, uploaded_bytes: int) -> None:
        self.add_distribution_metric("git_requests.objects_pack_files", uploaded_files)
        self.add_distribution_metric("git_requests.objects_pack_bytes", uploaded_bytes)


@dataclasses.dataclass
class TelemetryAPIRequestMetrics:
    telemetry_api: TelemetryAPI
    count: str
    duration: str
    response_bytes: t.Optional[str]
    error: str

    def record_request(
        self, seconds: float, response_bytes: t.Optional[int], compressed_response: bool, error: t.Optional[ErrorType]
    ) -> None:
        self.telemetry_api.add_count_metric(self.count, 1)
        self.telemetry_api.add_distribution_metric(self.duration, seconds)
        if response_bytes is not None and self.response_bytes is not None:
            # We don't always want to record response bytes (for settings requests), so assume that no metric name
            # means we don't want to record it.
            response_tags = {"rs_compressed": compressed_response}
            self.telemetry_api.add_distribution_metric(self.response_bytes, response_bytes, response_tags)

        if error is not None:
            self.record_error(error)

    def record_error(self, error: ErrorType) -> None:
        self.telemetry_api.add_count_metric(self.error, 1, {"error_type": error})
