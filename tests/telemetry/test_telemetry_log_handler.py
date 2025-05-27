import sys

from unittest.mock import patch
import pytest

from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.telemetry.writer import TelemetryWriter


def test_add_integration_error_log():
    """Test add_integration_error_log functionality with real stack trace"""
    from ddtrace.settings._telemetry import config

    writer = TelemetryWriter(is_periodic=False)
    writer._logs.clear()

    try:
        raise ValueError("Test exception")
    except ValueError as e:
        writer.add_integration_error_log("Test error message", e)

        assert len(writer._logs) == 1

        log_entry = list(writer._logs)[0]
        assert log_entry["level"] == TELEMETRY_LOG_LEVEL.ERROR.value
        assert log_entry["message"] == "Test error message"

        stack_trace = log_entry["stack_trace"]
        expected_lines = [
            "Traceback (most recent call last):",
            '  File "<redacted>/test_telemetry_log_handler.py", line 18, in test_add_integration_error_log',
            '    raise ValueError("Test exception")',
            "builtins.ValueError: Test exception"
        ]
        for expected_line in expected_lines:
            assert expected_line in stack_trace


def test_add_integration_error_log_with_log_collection_disabled():
    """Test that add_integration_error_log respects LOG_COLLECTION_ENABLED setting"""
    from ddtrace.settings._telemetry import config
    config.LOG_COLLECTION_ENABLED = False

    writer = TelemetryWriter(is_periodic=False)
    writer._logs.clear()

    try:
        raise ValueError("Test exception")
    except ValueError as e:
        writer.add_integration_error_log("Test error message", e)

        # No logs should be added when log collection is disabled
        assert len(writer._logs) == 0


def test_format_stack_trace_none():
    """Test stack trace formatting with no exception"""
    writer = TelemetryWriter(is_periodic=False)

    stack_trace = writer._format_stack_trace(None)
    assert stack_trace is None

@pytest.mark.parametrize(
    "filename,redacted_filename",
    [
        ("/path/to/file.py", "<redacted>/file.py"),
        ("/path/to/ddtrace/contrib/flask/file.py", "<redacted>/ddtrace/contrib/flask/file.py"),
        ("/path/to/dd-trace-something/file.py", "<redacted>/file.py")
    ],
)
def test_should_redact(filename, redacted_filename):
    """Test file redaction logic"""
    writer = TelemetryWriter(is_periodic=False)
    assert writer._redact_filename(filename) == redacted_filename

