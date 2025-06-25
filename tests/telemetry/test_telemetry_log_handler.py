import logging
import sys
from unittest.mock import ANY
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.telemetry.logging import DDTelemetryLogHandler


@pytest.fixture
def telemetry_writer():
    return Mock()


@pytest.fixture
def handler(telemetry_writer):
    return DDTelemetryLogHandler(telemetry_writer)


@pytest.fixture
def error_record():
    return logging.LogRecord(
        name="test_logger",
        level=logging.ERROR,
        pathname="/path/to",
        lineno=42,
        msg="Test error message %s",
        args=("arg1",),
        exc_info=None,
        filename="test.py",
    )


@pytest.fixture
def exc_info():
    try:
        raise ValueError("Test exception")
    except ValueError:
        return sys.exc_info()


def test_handler_initialization(handler, telemetry_writer):
    """Test handler initialization"""
    assert isinstance(handler, logging.Handler)
    assert handler.telemetry_writer == telemetry_writer


def test_emit_error_level(handler, error_record):
    """Test handling of ERROR level logs"""
    handler.emit(error_record)

    handler.telemetry_writer.add_error.assert_called_once_with(1, "Test error message arg1", ANY, 42)


def test_emit_ddtrace_contrib_error(handler, exc_info):
    """Test handling of ddtrace.contrib errors with stack trace"""
    record = logging.LogRecord(
        name="ddtrace.contrib.test",
        level=logging.ERROR,
        pathname="/path/to",
        lineno=42,
        msg="Test error message",
        args=(),
        exc_info=exc_info,
        filename="test.py",
    )

    handler.emit(record)

    handler.telemetry_writer.add_log.assert_called_once()
    args = handler.telemetry_writer.add_log.call_args.args
    assert args[0] == TELEMETRY_LOG_LEVEL.ERROR
    assert args[1] == "Test error message"


@pytest.mark.parametrize(
    "level,expected_telemetry_level",
    [
        (logging.WARNING, TELEMETRY_LOG_LEVEL.WARNING),
        (logging.INFO, TELEMETRY_LOG_LEVEL.DEBUG),
        (logging.DEBUG, TELEMETRY_LOG_LEVEL.DEBUG),
    ],
)
def test_emit_ddtrace_contrib_levels(handler, level, expected_telemetry_level, exc_info):
    """Test handling of ddtrace.contrib logs at different levels"""
    record = logging.LogRecord(
        name="ddtrace.contrib.test",
        level=level,
        pathname="/path/to",
        lineno=42,
        msg="Test message",
        args=(),
        exc_info=exc_info,
        filename="test.py",
    )

    handler.emit(record)

    args = handler.telemetry_writer.add_log.call_args.args
    assert args[0] == expected_telemetry_level


def test_emit_ddtrace_contrib_no_stack_trace(handler):
    """Test handling of ddtrace.contrib logs without stack trace"""
    record = logging.LogRecord(
        name="ddtrace.contrib.test",
        level=logging.WARNING,
        pathname="/path/to",
        lineno=42,
        msg="Test warning message",
        args=(),
        exc_info=None,
        filename="test.py",
    )

    handler.emit(record)
    handler.telemetry_writer.add_log.assert_not_called()


def test_format_stack_trace_none(handler):
    """Test stack trace formatting with no exception info"""
    assert handler._format_stack_trace(None) is None


def test_format_stack_trace_redaction(handler, exc_info):
    """Test stack trace redaction for non-ddtrace files"""
    formatted_trace = handler._format_stack_trace(exc_info)
    assert "<REDACTED>" in formatted_trace


@pytest.mark.parametrize(
    "filename,should_redact",
    [
        ("/path/to/file.py", True),
        ("/path/to/ddtrace/file.py", False),
        ("/usr/local/lib/python3.8/site-packages/ddtrace/core.py", False),
        ("/random/path/test.py", True),
    ],
)
def test_should_redact(handler, filename, should_redact):
    """Test file redaction logic"""
    assert handler._should_redact(filename) == should_redact


def test_format_file_path_value_error(handler):
    """Test file path formatting when relpath raises ValueError"""
    with patch("os.path.relpath", side_effect=ValueError):
        filename = "/some/path/file.py"
        assert handler._format_file_path(filename) == filename
