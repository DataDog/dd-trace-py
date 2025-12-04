from unittest.mock import Mock
from unittest.mock import call

from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.testing.internal.telemetry import ErrorType
from ddtrace.testing.internal.telemetry import TelemetryAPI


class TestTelemetry:
    def test_record_request(self) -> None:
        telemetry_api = TelemetryAPI(connector_setup=Mock())

        mock_writer = Mock()
        telemetry_api.writer = mock_writer

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

        assert mock_writer.add_count_metric.call_args_list == [
            call(TELEMETRY_NAMESPACE.CIVISIBILITY, "known_tests.request", 1, None),
            call(
                TELEMETRY_NAMESPACE.CIVISIBILITY,
                "known_tests.request_errors",
                1,
                (("error_type", ErrorType.CODE_4XX.value),),
            ),
        ]

        assert mock_writer.add_distribution_metric.call_args_list == [
            call(TELEMETRY_NAMESPACE.CIVISIBILITY, "known_tests.request_ms", 1.41, None),
            call(TELEMETRY_NAMESPACE.CIVISIBILITY, "known_tests.response_bytes", 42, ()),
        ]

    def test_record_request_without_response_bytes(self) -> None:
        telemetry_api = TelemetryAPI(connector_setup=Mock())

        mock_writer = Mock()
        telemetry_api.writer = mock_writer

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

        assert mock_writer.add_count_metric.call_args_list == [
            call(TELEMETRY_NAMESPACE.CIVISIBILITY, "known_tests.request", 1, None),
            call(
                TELEMETRY_NAMESPACE.CIVISIBILITY,
                "known_tests.request_errors",
                1,
                (("error_type", ErrorType.CODE_4XX.value),),
            ),
        ]

        assert mock_writer.add_distribution_metric.call_args_list == [
            call(TELEMETRY_NAMESPACE.CIVISIBILITY, "known_tests.request_ms", 1.41, None),
        ]

    def test_record_request_without_error(self) -> None:
        telemetry_api = TelemetryAPI(connector_setup=Mock())

        mock_writer = Mock()
        telemetry_api.writer = mock_writer

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

        assert mock_writer.add_count_metric.call_args_list == [
            call(TELEMETRY_NAMESPACE.CIVISIBILITY, "known_tests.request", 1, None),
        ]

        assert mock_writer.add_distribution_metric.call_args_list == [
            call(TELEMETRY_NAMESPACE.CIVISIBILITY, "known_tests.request_ms", 1.41, None),
            call(TELEMETRY_NAMESPACE.CIVISIBILITY, "known_tests.response_bytes", 42, ()),
        ]
