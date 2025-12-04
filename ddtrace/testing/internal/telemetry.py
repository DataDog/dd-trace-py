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
from ddtrace.testing.internal.test_data import TestModule
from ddtrace.testing.internal.test_data import TestRun
from ddtrace.testing.internal.test_data import TestSession
from ddtrace.testing.internal.test_data import TestSuite


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

    # Coverage.

    def record_coverage_started(self, test_framework: str, coverage_library: str) -> None:
        log.debug("Recording code coverage started telemetry: %s, %s", test_framework, coverage_library)
        tags = (("library", coverage_library), ("test_framework", test_framework))
        self.writer.add_count_metric(CIVISIBILITY, "code_coverage_started", 1, tags)

    def record_coverage_finished(self, test_framework: str, coverage_library: str) -> None:
        log.debug("Recording code coverage finished telemetry: %s, %s", test_framework, coverage_library)
        tags = (("library", coverage_library), ("test_framework", test_framework))
        self.writer.add_count_metric(CIVISIBILITY, "code_coverage_finished", 1, tags)

    def record_coverage_is_empty(self) -> None:
        log.debug("Recording code coverage is empty")
        self.writer.add_count_metric(CIVISIBILITY, "code_coverage.is_empty", 1)

    def record_coverage_files(self, count_files: int) -> None:
        log.debug("Recording code coverage files telemetry: %s", count_files)
        self.writer.add_distribution_metric(CIVISIBILITY, "code_coverage.files", count_files)

    # API calls.

    def record_known_tests_count(self, count: int) -> None:
        log.debug("Recording known tests count telemetry: %s", count)
        self.writer.add_distribution_metric(CIVISIBILITY, "known_tests.response_tests", count)

    def record_skippable_count(self, count: int, level: ITRSkippingLevel) -> None:
        log.debug("Recording skippable %s count: %s", level.value, count)
        skippable_count_metric = (
            "itr_skippable_tests.response_suites"
            if level == ITRSkippingLevel.SUITE
            else "itr_skippable_tests.response_tests"
        )
        self.writer.add_count_metric(CIVISIBILITY, skippable_count_metric, count)

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
        self.writer.add_count_metric(CIVISIBILITY, "git_requests.settings_response", 1, tuple(tags))

    def record_test_management_tests_count(self, count: int) -> None:
        log.debug("Recording Test Management tests count telemetry: %s", count)
        self.writer.add_distribution_metric(CIVISIBILITY, "test_management_tests.response_tests", count)

    def record_git_command(self, command: GitTelemetry, elapsed_seconds: float, exit_code: int) -> None:
        log.debug("Recording git command telemetry: %s, %s, %s", command.value, elapsed_seconds, exit_code)

        tags = (("command", command.value),)
        self.writer.add_count_metric(CIVISIBILITY, "git.command", 1, tags)
        self.writer.add_distribution_metric(CIVISIBILITY, "git.command_ms", elapsed_seconds * 1000, tags)

        if exit_code:
            error_tags = (("command", command.value), ("exit_code", str(exit_code)))
            self.writer.add_count_metric(CIVISIBILITY, "git.command_errors", 1, error_tags)

    # Event payloads sent by writers.

    def record_event_payload(
        self,
        endpoint: str,
        payload_size: int,
        request_seconds: float,
        events_count: int,
        serialization_seconds: float,
        error: t.Optional[ErrorType],
    ) -> None:
        log.debug(
            "Recording event payload: endpoint %s, payload size: %s, request seconds: %.3f, events count: %s, "
            "serialization seconds: %.3f, error: %s",
            endpoint,
            payload_size,
            request_seconds,
            events_count,
            serialization_seconds,
            error,
        )

        tags = (("endpoint", endpoint),)

        self.writer.add_distribution_metric(CIVISIBILITY, "endpoint_payload.bytes", payload_size, tags)
        self.writer.add_count_metric(CIVISIBILITY, "endpoint_payload.requests", 1, tags)
        self.writer.add_distribution_metric(CIVISIBILITY, "endpoint_payload.requests_ms", request_seconds * 1000, tags)
        self.writer.add_distribution_metric(CIVISIBILITY, "endpoint_payload.events_count", events_count, tags)
        self.writer.add_distribution_metric(
            CIVISIBILITY, "endpoint_payload.events_serialization_ms", serialization_seconds * 1000, tags
        )

        if error:
            self.record_event_payload_error(endpoint, error)

    def record_event_payload_error(self, endpoint: str, error: ErrorType) -> None:
        # `endpoint_payload.requests_errors` accepts a different set of error types, so we need to convert them here.
        if error == ErrorType.TIMEOUT:
            endpoint_error = "timeout"
        elif error in (ErrorType.CODE_4XX, ErrorType.CODE_5XX):
            endpoint_error = "status_code"
        else:
            endpoint_error = "network"

        tags = (("endpoint", endpoint), ("error_type", endpoint_error))
        self.writer.add_count_metric(CIVISIBILITY, "endpoint_payload.requests_errors", 1, tags)

    # Test creation/finish events.

    def record_test_created(self, test_framework: str, test_run: TestRun) -> None:
        tags = [("event_type", EventType.TEST), ("test_framework", test_framework)]
        if test_run.is_benchmark():
            tags.append(("is_benchmark", "true"))

        log.debug("Recording test event created: test_framework=%s, test=%s, tags=%s", test_framework, test_run, tags)
        self.writer.add_count_metric(CIVISIBILITY, "event_created", 1, tuple(tags))

    def record_test_finished(
        self, test_framework: str, test_run: TestRun, ci_provider_name: t.Optional[str], is_auto_injected: bool
    ) -> None:
        tags = [("event_type", EventType.TEST), ("test_framework", test_framework)]
        if test_run.is_benchmark():
            tags.append(("is_benchmark", "true"))
        if test_run.test.is_new():
            tags.append(("is_new", "true"))
        if test_run.is_retry():
            tags.append(("is_retry", "true"))
        if test_run.is_rum():
            tags.append(("is_rum", "true"))
        if (browser_driver := test_run.get_browser_driver()) is not None:
            tags.append(("browser_driver", browser_driver))
        if (efd_abort_reason := test_run.get_early_flake_detection_abort_reason()) is not None:
            tags.append(("early_flake_detection_abort_reason", efd_abort_reason))
        if test_run.test.is_quarantined():
            tags.append(("is_quarantined", "true"))
        if test_run.test.is_disabled():
            tags.append(("is_disabled", "true"))
        if test_run.test.is_attempt_to_fix():
            tags.append(("is_attempt_to_fix", "true"))
        if test_run.has_failed_all_retries():
            tags.append(("has_failed_all_retries", "true"))
        if ci_provider_name:
            tags.append(("provider_name", ci_provider_name))
        if is_auto_injected:
            tags.append(("auto_injected", "true"))

        log.debug("Recording test event created: test_framework=%s, test=%s, tags=%s", test_framework, test_run, tags)
        self.writer.add_count_metric(CIVISIBILITY, "event_finished", 1, tuple(tags))

    def record_suite_created(self, test_framework: str, suite: TestSuite) -> None: ...

    def record_suite_finished(self, test_framework: str, suite: TestSuite) -> None: ...

    def record_module_created(self, test_framework: str, module: TestModule) -> None: ...

    def record_module_finished(self, test_framework: str, module: TestModule) -> None: ...

    def record_session_created(self, test_framework: str, session: TestSession) -> None: ...

    def record_session_finished(self, test_framework: str, session: TestSession) -> None: ...


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
        self.telemetry_api.writer.add_count_metric(CIVISIBILITY, self.count, 1)
        self.telemetry_api.writer.add_distribution_metric(CIVISIBILITY, self.duration, seconds)
        if response_bytes is not None and self.response_bytes is not None:
            # We don't always want to record response bytes (for settings requests), so assume that no metric name
            # means we don't want to record it.
            response_tags = (("rs_compressed", "true"),) if compressed_response else None
            self.telemetry_api.writer.add_distribution_metric(
                CIVISIBILITY, self.response_bytes, response_bytes, response_tags
            )

        if error is not None:
            self.record_error(error)

    def record_error(self, error: ErrorType) -> None:
        log.debug("Recording Test Optimization request error telemetry: %s", error)
        self.telemetry_api.writer.add_count_metric(CIVISIBILITY, self.error, 1, (("error_type", error),))
