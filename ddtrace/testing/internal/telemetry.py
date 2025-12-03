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


if t.TYPE_CHECKING:
    from ddtrace.testing.internal.http import BackendConnectorSetup


log = logging.getLogger(__name__)


class ErrorType(str, Enum):
    TIMEOUT = "timeout"
    NETWORK = "network"
    CODE_4XX = "status_code_4xx_response"
    CODE_5XX = "status_code_5xx_response"
    BAD_JSON = "bad_json"
    UNKNOWN = "unknown"


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
        TelemetryAPI._instance = self

    @classmethod
    def get(cls) -> TelemetryAPI:
        if not cls._instance:
            raise RuntimeError("Telemetry API is not initialized yet; this is a bug")
        return cls._instance

    def with_request_metric_names(
        self, count: str, duration: str, response_bytes: t.Optional[str], error: str
    ) -> TelemetryAPIRequestMetrics:
        return TelemetryAPIRequestMetrics(
            telemetry_api=self, count=count, duration=duration, response_bytes=response_bytes, error=error
        )

    def finish(self) -> None:
        self.writer.periodic(force_flush=True)

    def record_coverage_started(self, test_framework: str, coverage_library: str) -> None:
        log.debug("Recording code coverage started telemetry: %s, %s", test_framework, coverage_library)
        tags = (("library", coverage_library), ("test_framework", test_framework))
        self.writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, "code_coverage_started", 1, tags)

    def record_coverage_finished(self, test_framework: str, coverage_library: str) -> None:
        log.debug("Recording code coverage finished telemetry: %s, %s", test_framework, coverage_library)
        tags = (("library", coverage_library), ("test_framework", test_framework))
        self.writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, "code_coverage_finished", 1, tags)

    def record_coverage_is_empty(self) -> None:
        log.debug("Recording code coverage is empty")
        self.writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, "code_coverage.is_empty", 1)

    def record_coverage_files(self, count_files: int) -> None:
        log.debug("Recording code coverage files telemetry: %s", count_files)
        self.writer.add_distribution_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, "code_coverage.files", count_files)

    def record_known_tests_count(self, count: int) -> None:
        log.debug("Recording known tests count telemetry: %s", count)
        self.writer.add_distribution_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, "known_tests.response_tests", count)

    def record_skippable_count(self, count: int, level: ITRSkippingLevel) -> None:
        log.debug("Recording skippable %s count: %s", level.value, count)
        skippable_count_metric = (
            "itr_skippable_tests.response_suites"
            if level == ITRSkippingLevel.SUITE
            else "itr_skippable_tests.response_tests"
        )
        self.writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, skippable_count_metric, count)

    def record_settings(self, settings: Settings) -> None:
        tags = []

        if settings.coverage_enabled:
            tags.append(("coverage_enabled", "true"))
        if settings.skipping_enabled:
            tags.append(("itrskip_enabled", "true"))
        if settings.require_git:
            tags.append(("require_git", "true"))
        if settings.itr_enabled:
            tags.append(("itr_enabled", "true"))
        if settings.known_tests_enabled:
            tags.append(("known_tests_enabled", "true"))

        if settings.auto_test_retries.enabled:
            tags.append(("flaky_test_retries_enabled", "true"))
        if settings.early_flake_detection.enabled:
            tags.append(("early_flake_detection_enabled", "true"))
        if settings.test_management.enabled:
            tags.append(("test_management_enabled", "true"))

        log.debug("Recording test settings telemetry: %s", tags)
        self.writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, "git_requests.settings_response", 1, tuple(tags))

    def record_test_management_tests_count(self, count: int) -> None:
        log.debug("Recording Test Management tests count telemetry: %s", count)
        self.writer.add_distribution_metric(
            TELEMETRY_NAMESPACE.CIVISIBILITY, "test_management_tests.response_tests", count
        )

    def record_git_command(self, command: GitTelemetry, elapsed_seconds: float, exit_code: int) -> None:
        log.debug("Recording git command telemetry: %s, %s, %s", command.value, elapsed_seconds, exit_code)

        tags = (("command", command.value),)
        self.writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, "git.command", 1, tags)
        self.writer.add_distribution_metric(
            TELEMETRY_NAMESPACE.CIVISIBILITY, "git.command_ms", elapsed_seconds * 1000, tags
        )

        if exit_code:
            error_tags = (("command", command.value), ("exit_code", str(exit_code)))
            self.writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, "git.command_errors", 1, error_tags)


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
        log.debug(
            "Recording Test Optimization telemetry for %s: %s %s %s %s",
            self.count,
            seconds,
            response_bytes,
            compressed_response,
            error,
        )
        self.telemetry_api.writer.add_count_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, self.count, 1)
        self.telemetry_api.writer.add_distribution_metric(TELEMETRY_NAMESPACE.CIVISIBILITY, self.duration, seconds)
        if response_bytes is not None and self.response_bytes is not None:
            # We don't always want to record response bytes (for settings requests), so assume that no metric name
            # means we don't want to record it.
            response_tags = (("rs_compressed", "true"),) if compressed_response else None
            self.telemetry_api.writer.add_distribution_metric(
                TELEMETRY_NAMESPACE.CIVISIBILITY, self.response_bytes, response_bytes, response_tags
            )

        if error is not None:
            self.record_error(error)

    def record_error(self, error: ErrorType) -> None:
        log.debug("Recording Test Optimization request error telemetry: %s", error)
        self.telemetry_api.writer.add_count_metric(
            TELEMETRY_NAMESPACE.CIVISIBILITY, self.error, 1, (("error_type", error),)
        )
