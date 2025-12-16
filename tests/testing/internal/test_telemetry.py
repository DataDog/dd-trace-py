import typing as t
from unittest.mock import Mock
from unittest.mock import call

import pytest

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.testing.internal.settings_data import AutoTestRetriesSettings
from ddtrace.testing.internal.settings_data import EarlyFlakeDetectionSettings
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.settings_data import TestManagementSettings
from ddtrace.testing.internal.telemetry import ErrorType
from ddtrace.testing.internal.telemetry import GitTelemetry
from ddtrace.testing.internal.telemetry import TelemetryAPI
from ddtrace.testing.internal.test_data import ITRSkippingLevel
from ddtrace.testing.internal.test_data import TestSession
from ddtrace.testing.internal.test_data import TestTag


CIVISIBILITY = TELEMETRY_NAMESPACE.CIVISIBILITY


@pytest.fixture
def telemetry_api() -> t.Generator[TelemetryAPI, None, None]:
    api = TelemetryAPI(connector_setup=Mock())

    mock_writer = Mock()
    api.writer = mock_writer

    yield api


class TestTelemetry:
    def test_record_request(self, telemetry_api: TelemetryAPI) -> None:
        request_telemetry = telemetry_api.with_request_metric_names(
            count="known_tests.request",
            duration="known_tests.request_ms",
            response_bytes="known_tests.response_bytes",
            error="known_tests.request_errors",
        )

        request_telemetry.record_request(
            seconds=1.41,
            response_bytes=42,
            compressed_response=False,
            error=ErrorType.CODE_4XX,
        )

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "known_tests.request", 1, ()),
            call(
                CIVISIBILITY,
                "known_tests.request_errors",
                1,
                (("error_type", ErrorType.CODE_4XX.value),),
            ),
        ]

        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "known_tests.request_ms", 1.41, ()),
            call(CIVISIBILITY, "known_tests.response_bytes", 42, ()),
        ]

    def test_record_request_without_response_bytes(self, telemetry_api: TelemetryAPI) -> None:
        request_telemetry = telemetry_api.with_request_metric_names(
            count="known_tests.request",
            duration="known_tests.request_ms",
            response_bytes=None,
            error="known_tests.request_errors",
        )

        request_telemetry.record_request(
            seconds=1.41,
            response_bytes=42,
            compressed_response=False,
            error=ErrorType.CODE_4XX,
        )

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "known_tests.request", 1, ()),
            call(
                CIVISIBILITY,
                "known_tests.request_errors",
                1,
                (("error_type", ErrorType.CODE_4XX.value),),
            ),
        ]

        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "known_tests.request_ms", 1.41, ()),
        ]

    def test_record_request_without_error(self, telemetry_api: TelemetryAPI) -> None:
        request_telemetry = telemetry_api.with_request_metric_names(
            count="known_tests.request",
            duration="known_tests.request_ms",
            response_bytes="known_tests.response_bytes",
            error="known_tests.request_errors",
        )

        request_telemetry.record_request(
            seconds=1.41,
            response_bytes=42,
            compressed_response=False,
            error=None,
        )

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "known_tests.request", 1, ()),
        ]

        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "known_tests.request_ms", 1.41, ()),
            call(CIVISIBILITY, "known_tests.response_bytes", 42, ()),
        ]

    def test_record_coverage_started(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_coverage_started(test_framework="pytest", coverage_library="ddtrace")

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(
                CIVISIBILITY,
                "code_coverage_started",
                1,
                (("library", "ddtrace"), ("test_framework", "pytest")),
            )
        ]

    def test_record_coverage_finished(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_coverage_finished(test_framework="pytest", coverage_library="ddtrace")

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(
                CIVISIBILITY,
                "code_coverage_finished",
                1,
                (("library", "ddtrace"), ("test_framework", "pytest")),
            )
        ]

    def test_record_coverage_is_empty(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_coverage_is_empty()

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "code_coverage.is_empty", 1, ())
        ]

    def test_record_coverage_files(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_coverage_files(42)

        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "code_coverage.files", 42, ())
        ]

    def test_record_known_tests_count(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_known_tests_count(42)

        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "known_tests.response_tests", 42, ())
        ]

    def test_record_skippable_tests_count(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_skippable_count(42, ITRSkippingLevel.TEST)

        # count metric, not distribution metric, for inexplicable reasons
        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "itr_skippable_tests.response_tests", 42, ())
        ]

    def test_record_skippable_suites_count(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_skippable_count(42, ITRSkippingLevel.SUITE)

        # count metric, not distribution metric, for inexplicable reasons
        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "itr_skippable_tests.response_suites", 42, ())
        ]

    def test_record_settings_all_enabled(self, telemetry_api: TelemetryAPI) -> None:
        settings = Settings(
            early_flake_detection=EarlyFlakeDetectionSettings(enabled=True),
            auto_test_retries=AutoTestRetriesSettings(enabled=True),
            test_management=TestManagementSettings(enabled=True),
            known_tests_enabled=True,
            coverage_enabled=True,
            skipping_enabled=True,
            require_git=True,
            itr_enabled=True,
        )
        telemetry_api.record_settings(settings)

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(
                CIVISIBILITY,
                "git_requests.settings_response",
                1,
                (
                    ("coverage_enabled", "true"),
                    ("itrskip_enabled", "true"),
                    ("require_git", "true"),
                    ("itr_enabled", "true"),
                    ("known_tests_enabled", "true"),
                    ("flaky_test_retries_enabled", "true"),
                    ("early_flake_detection_enabled", "true"),
                    ("test_management_enabled", "true"),
                ),
            )
        ]

    def test_record_settings_some_enabled(self, telemetry_api: TelemetryAPI) -> None:
        settings = Settings(
            early_flake_detection=EarlyFlakeDetectionSettings(enabled=False),
            auto_test_retries=AutoTestRetriesSettings(enabled=True),
            test_management=TestManagementSettings(enabled=False),
            known_tests_enabled=True,
            coverage_enabled=False,
            skipping_enabled=False,
            require_git=True,
            itr_enabled=False,
        )
        telemetry_api.record_settings(settings)

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(
                CIVISIBILITY,
                "git_requests.settings_response",
                1,
                (
                    ("require_git", "true"),
                    ("known_tests_enabled", "true"),
                    ("flaky_test_retries_enabled", "true"),
                ),
            )
        ]

    def test_record_test_management_tests_count(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_test_management_tests_count(42)

        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "test_management_tests.response_tests", 42, ())
        ]

    def test_record_git_command_ok(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_git_command(command=GitTelemetry.GET_REPOSITORY, elapsed_seconds=1.2, exit_code=0)

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "git.command", 1, (("command", "get_repository"),))
        ]
        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "git.command_ms", 1200, (("command", "get_repository"),))
        ]

    def test_record_git_command_error(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_git_command(command=GitTelemetry.GET_REPOSITORY, elapsed_seconds=1.2, exit_code=4)

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "git.command", 1, (("command", "get_repository"),)),
            call(
                CIVISIBILITY,
                "git.command_errors",
                1,
                (
                    ("command", "get_repository"),
                    ("exit_code", "4"),
                ),
            ),
        ]
        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "git.command_ms", 1200, (("command", "get_repository"),))
        ]

    def test_record_event_payload_ok(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_event_payload(
            endpoint="test_cycle",
            payload_size=613,
            request_seconds=3.14,
            events_count=42,
            error=None,
        )

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "endpoint_payload.requests", 1, (("endpoint", "test_cycle"),))
        ]
        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "endpoint_payload.bytes", 613, (("endpoint", "test_cycle"),)),
            call(CIVISIBILITY, "endpoint_payload.requests_ms", 3140, (("endpoint", "test_cycle"),)),
            call(CIVISIBILITY, "endpoint_payload.events_count", 42, (("endpoint", "test_cycle"),)),
        ]

    @pytest.mark.parametrize(
        "http_error_type,telemetry_error_type",
        [
            (ErrorType.TIMEOUT, "timeout"),
            (ErrorType.NETWORK, "network"),
            (ErrorType.CODE_4XX, "status_code"),
            (ErrorType.CODE_5XX, "status_code"),
            (ErrorType.BAD_JSON, "network"),
            (ErrorType.UNKNOWN, "network"),
        ],
    )
    def test_record_event_payload_error(
        self, telemetry_api: TelemetryAPI, http_error_type: ErrorType, telemetry_error_type: str
    ) -> None:
        telemetry_api.record_event_payload(
            endpoint="test_cycle",
            payload_size=613,
            request_seconds=3.14,
            events_count=42,
            error=http_error_type,
        )

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "endpoint_payload.requests", 1, (("endpoint", "test_cycle"),)),
            call(
                CIVISIBILITY,
                "endpoint_payload.requests_errors",
                1,
                (
                    ("endpoint", "test_cycle"),
                    ("error_type", telemetry_error_type),
                ),
            ),
        ]
        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "endpoint_payload.bytes", 613, (("endpoint", "test_cycle"),)),
            call(CIVISIBILITY, "endpoint_payload.requests_ms", 3140, (("endpoint", "test_cycle"),)),
            call(CIVISIBILITY, "endpoint_payload.events_count", 42, (("endpoint", "test_cycle"),)),
        ]

    def test_record_event_payload_serialization_seconds(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_event_payload_serialization_seconds("test_cycle", 0.5)

        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "endpoint_payload.events_serialization_ms", 500, (("endpoint", "test_cycle"),)),
        ]

    def test_record_test_created(self, telemetry_api: TelemetryAPI) -> None:
        session = TestSession("pytest")
        module, _ = session.get_or_create_child("module")
        suite, _ = module.get_or_create_child("suite")
        test, _ = suite.get_or_create_child("test")
        test_run = test.make_test_run()

        telemetry_api.record_test_created(test_framework="pytest", test_run=test_run)

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(
                CIVISIBILITY,
                "event_created",
                1,
                (
                    ("event_type", "test"),
                    ("test_framework", "pytest"),
                ),
            )
        ]

    def test_record_test_finished(self, telemetry_api: TelemetryAPI) -> None:
        session = TestSession("pytest")
        module, _ = session.get_or_create_child("module")
        suite, _ = module.get_or_create_child("suite")
        test, _ = suite.get_or_create_child("test")
        test_run = test.make_test_run()

        telemetry_api.record_test_finished(
            test_framework="pytest", test_run=test_run, ci_provider_name="gitlab", is_auto_injected=True
        )

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(
                CIVISIBILITY,
                "event_finished",
                1,
                (
                    ("event_type", "test"),
                    ("test_framework", "pytest"),
                    ("provider_name", "gitlab"),
                    ("auto_injected", "true"),
                ),
            )
        ]

    def test_record_test_finished_all_the_tags(self, telemetry_api: TelemetryAPI) -> None:
        session = TestSession("pytest")
        module, _ = session.get_or_create_child("module")
        suite, _ = module.get_or_create_child("suite")
        test, _ = suite.get_or_create_child("test")
        test.set_attributes(
            is_new=True,
            is_quarantined=True,
            is_disabled=True,
            is_attempt_to_fix=True,
        )
        test.set_early_flake_detection_abort_reason("slow")
        _initial_test_run = test.make_test_run()
        retry_test_run = test.make_test_run()
        retry_test_run.is_benchmark = lambda: True
        retry_test_run.tags[TestTag.IS_RUM_ACTIVE] = "true"
        retry_test_run.tags[TestTag.BROWSER_DRIVER] = "selenium"
        retry_test_run.tags[TestTag.HAS_FAILED_ALL_RETRIES] = "true"

        telemetry_api.record_test_finished(
            test_framework="pytest", test_run=retry_test_run, ci_provider_name="gitlab", is_auto_injected=True
        )

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(
                CIVISIBILITY,
                "event_finished",
                1,
                (
                    ("event_type", "test"),
                    ("test_framework", "pytest"),
                    ("is_benchmark", "true"),
                    ("is_new", "true"),
                    ("is_retry", "true"),
                    ("is_rum", "true"),
                    ("browser_driver", "selenium"),
                    ("early_flake_detection_abort_reason", "slow"),
                    ("is_quarantined", "true"),
                    ("is_disabled", "true"),
                    ("is_attempt_to_fix", "true"),
                    ("has_failed_all_retries", "true"),
                    ("provider_name", "gitlab"),
                    ("auto_injected", "true"),
                ),
            )
        ]

    def test_record_suite_created(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_suite_created(test_framework="pytest")

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "event_created", 1, (("event_type", "suite"), ("test_framework", "pytest")))
        ]

    def test_record_suite_finished(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_suite_finished(test_framework="pytest")

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "event_finished", 1, (("event_type", "suite"), ("test_framework", "pytest")))
        ]

    def test_record_module_created(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_module_created(test_framework="pytest")

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "event_created", 1, (("event_type", "module"), ("test_framework", "pytest")))
        ]

    def test_record_module_finished(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_module_finished(test_framework="pytest")

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(CIVISIBILITY, "event_finished", 1, (("event_type", "module"), ("test_framework", "pytest")))
        ]

    def test_record_session_created(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_session_created(test_framework="pytest", has_codeowners=True, is_unsupported_ci=True)

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(
                CIVISIBILITY,
                "event_created",
                1,
                (
                    ("event_type", "session"),
                    ("test_framework", "pytest"),
                    ("has_codeowners", "true"),
                    ("is_unsupported_ci", "true"),
                ),
            )
        ]

    def test_record_session_finished(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_session_finished(
            test_framework="pytest", has_codeowners=True, is_unsupported_ci=True, efd_abort_reason="faulty"
        )

        assert telemetry_api.writer.add_count_metric.call_args_list == [
            call(
                CIVISIBILITY,
                "event_finished",
                1,
                (
                    ("event_type", "session"),
                    ("test_framework", "pytest"),
                    ("has_codeowners", "true"),
                    ("is_unsupported_ci", "true"),
                    ("early_flake_detection_abort_reason", "faulty"),
                ),
            )
        ]

    def test_record_git_pack_data(self, telemetry_api: TelemetryAPI) -> None:
        telemetry_api.record_git_pack_data(uploaded_files=5, uploaded_bytes=200)

        assert telemetry_api.writer.add_distribution_metric.call_args_list == [
            call(CIVISIBILITY, "git_requests.objects_pack_files", 5, ()),
            call(CIVISIBILITY, "git_requests.objects_pack_bytes", 200, ()),
        ]
