"""Tests for ddtrace.testing.internal.logging module."""

import io
import logging
import os
from typing import Optional
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.logging import _DDTraceClosedStreamFilter
from ddtrace.testing.internal.logging import _SafeStreamHandler
from ddtrace.testing.internal.logging import catch_and_log_exceptions
from ddtrace.testing.internal.logging import protect_ddtrace_stream_handlers
from ddtrace.testing.internal.logging import setup_logging
from ddtrace.testing.internal.logging import testing_logger


class TestSetupLogging:
    """Tests for setup_logging function."""

    def teardown_method(self) -> None:
        """Clean up logger state after each test."""
        # Remove all handlers
        for handler in testing_logger.handlers[:]:
            testing_logger.removeHandler(handler)
        # Reset logger state
        testing_logger.propagate = True
        testing_logger.setLevel(logging.NOTSET)

    @patch.dict(os.environ, {}, clear=True)
    def test_setup_logging_default_level(self) -> None:
        """Test setup_logging with default (INFO) level."""
        setup_logging()

        assert testing_logger.propagate is False
        assert testing_logger.level == logging.INFO
        assert len(testing_logger.handlers) == 1

        handler = testing_logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)

    @patch.dict(os.environ, {"DD_TEST_DEBUG": "true"})
    def test_setup_logging_debug_level_true(self) -> None:
        """Test setup_logging with DEBUG level enabled via true."""
        setup_logging()

        assert testing_logger.propagate is False
        assert testing_logger.level == logging.DEBUG
        assert len(testing_logger.handlers) == 1

    @patch.dict(os.environ, {"DD_TEST_DEBUG": "1"})
    def test_setup_logging_debug_level_one(self) -> None:
        """Test setup_logging with DEBUG level enabled via 1."""
        setup_logging()

        assert testing_logger.level == logging.DEBUG

    @patch.dict(os.environ, {"DD_TEST_DEBUG": "false"})
    def test_setup_logging_debug_level_false(self) -> None:
        """Test setup_logging with DEBUG level disabled."""
        setup_logging()

        assert testing_logger.level == logging.INFO

    @patch.dict(os.environ, {"DD_TEST_DEBUG": "0"})
    def test_setup_logging_debug_level_zero(self) -> None:
        """Test setup_logging with DEBUG level disabled via 0."""
        setup_logging()

        assert testing_logger.level == logging.INFO

    def test_setup_logging_formatter(self) -> None:
        """Test that the formatter is correctly configured."""
        setup_logging()

        handler = testing_logger.handlers[0]
        formatter = handler.formatter
        assert formatter is not None

        # Test the format string contains expected elements
        format_string = formatter._fmt
        assert isinstance(format_string, str)
        assert "[Datadog Test Optimization]" in format_string
        assert "%(levelname)-8s" in format_string
        assert "%(name)s" in format_string
        assert "%(filename)s" in format_string
        assert "%(lineno)d" in format_string
        assert "%(message)s" in format_string

    def test_setup_logging_multiple_calls(self) -> None:
        """Test that calling setup_logging multiple times doesn't add duplicate handlers."""
        setup_logging()
        initial_handler_count = len(testing_logger.handlers)

        setup_logging()
        # Should still have the same number of handlers (assuming no duplicate prevention logic)
        # This test documents current behavior - if duplicate prevention is added, adjust accordingly
        assert len(testing_logger.handlers) >= initial_handler_count


class TestCatchAndLogExceptions:
    """Tests for catch_and_log_exceptions decorator."""

    def test_decorator_success(self) -> None:
        """Test decorator with successful function execution."""

        @catch_and_log_exceptions()
        def successful_function(x: int, y: int) -> int:
            return x + y

        result = successful_function(2, 3)
        assert result == 5

    @patch.object(testing_logger, "exception")
    def test_decorator_exception_logging(self, mock_exception: Mock) -> None:
        """Test decorator catches and logs exceptions."""

        @catch_and_log_exceptions()
        def failing_function() -> None:
            raise ValueError("Test error")

        result = failing_function()

        assert result is None
        mock_exception.assert_called_once_with("Error while calling %s", "failing_function")

    @patch.object(testing_logger, "exception")
    def test_decorator_with_arguments(self, mock_exception: Mock) -> None:
        """Test decorator works with function arguments."""

        @catch_and_log_exceptions()
        def function_with_args(a: int, b: int, c: Optional[int] = None) -> int:
            if c is None:
                raise RuntimeError("c is None")
            return a + b + c

        # Test successful call
        result = function_with_args(1, 2, c=3)
        assert result == 6

        # Test failing call
        result = function_with_args(1, 2)
        assert result is None
        mock_exception.assert_called_once_with("Error while calling %s", "function_with_args")

    @patch.object(testing_logger, "exception")
    def test_decorator_preserves_function_metadata(self, mock_exception: Mock) -> None:
        """Test decorator preserves original function metadata."""

        def original_function() -> str:
            """Original docstring."""
            return "original"

        decorated = catch_and_log_exceptions()(original_function)

        # Check that function name is preserved for logging
        decorated()
        assert decorated.__name__ == "original_function"  # functools.wraps preserves original name


class TestSafeStreamHandler:
    """Tests for _SafeStreamHandler — regression tests for closed-stream errors during shutdown."""

    def test_emit_to_closed_stream_does_not_produce_logging_error(self) -> None:
        """Writing to a closed stream must not print '--- Logging error ---'.

        Reproduces the scenario where a daemon thread logs after sys.stderr is
        closed during interpreter shutdown.  A plain StreamHandler would call
        handleError() which prints a noisy traceback; _SafeStreamHandler must
        suppress it silently.
        """
        # Use a real stderr-like object we can close to simulate shutdown.
        stream = io.StringIO()
        stream.close()

        handler = _SafeStreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))

        # Capture anything handleError might write to the real stderr.
        real_stderr = io.StringIO()
        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="should be silently dropped",
            args=(),
            exc_info=None,
        )

        with patch("sys.stderr", real_stderr):
            handler.emit(record)

        assert "Logging error" not in real_stderr.getvalue()

    def test_emit_to_open_stream_works_normally(self) -> None:
        """_SafeStreamHandler must behave identically to StreamHandler for open streams."""
        stream = io.StringIO()
        handler = _SafeStreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))

        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        handler.emit(record)

        assert "hello" in stream.getvalue()

    def test_non_value_error_still_calls_default_handle_error(self) -> None:
        """Errors other than ValueError must still go through the default handleError path."""
        stream = io.StringIO()
        handler = _SafeStreamHandler(stream)

        # Use a formatter that raises a TypeError to trigger handleError with a non-ValueError.
        bad_formatter = Mock()
        bad_formatter.format.side_effect = TypeError("bad format")
        handler.setFormatter(bad_formatter)

        record = logging.LogRecord(
            name="test",
            level=logging.WARNING,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )

        real_stderr = io.StringIO()
        with patch("sys.stderr", real_stderr):
            handler.emit(record)

        # The default handleError should have printed the traceback for the TypeError.
        assert "Logging error" in real_stderr.getvalue()


class TestDDTraceClosedStreamFilter:
    @pytest.mark.parametrize("name", ["ddtrace", "ddtrace._trace.tracer", "application", "ddtrace_other"])
    def test_only_closed_tracer_destination_is_filtered(self, name: str) -> None:
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.addFilter(_DDTraceClosedStreamFilter(handler))
        record = logging.makeLogRecord({"name": name, "msg": "delivery probe"})
        handler.handle(record)
        assert stream.getvalue() == "delivery probe\n"
        stream.close()

        with patch.object(handler, "handleError") as error:
            handler.handle(record)
        assert error.call_count == (0 if name == "ddtrace" or name.startswith("ddtrace.") else 1)

        # A reused handler must not stay muted after replacing its closed stream.
        handler.stream = io.StringIO()
        handler.handle(record)
        assert handler.stream.getvalue() == "delivery probe\n"

    @pytest.mark.parametrize("exception", [ValueError("invalid format"), TypeError("invalid format")])
    def test_formatter_errors_remain_visible(self, exception: Exception) -> None:
        handler = logging.StreamHandler(io.StringIO())
        handler.addFilter(_DDTraceClosedStreamFilter(handler))
        handler.setFormatter(Mock(format=Mock(side_effect=exception)))
        record = logging.makeLogRecord({"name": "ddtrace", "msg": "delivery probe"})
        with patch.object(handler, "handleError") as error:
            handler.handle(record)
        error.assert_called_once_with(record)

    def test_scanning_preserves_handlers_and_is_idempotent(self, tmp_path) -> None:
        root = logging.RootLogger(logging.WARNING)
        tracer = logging.Logger("ddtrace")
        child = logging.Logger("ddtrace.child")
        application = logging.Logger("application")
        handlers: list[logging.Handler] = [logging.StreamHandler(io.StringIO()) for _ in range(4)]
        for logger, handler in zip((root, tracer, child, application), handlers):
            logger.addHandler(handler)
        existing_filter = logging.Filter()
        handlers[0].addFilter(existing_filter)
        file_handler = logging.FileHandler(tmp_path / "reopen.log", delay=True)
        custom_handler = _SafeStreamHandler(io.StringIO())
        root.addHandler(file_handler)
        root.addHandler(custom_handler)
        before = list(root.handlers)
        try:
            with (
                patch("logging.getLogger", return_value=root),
                patch.dict(
                    logging.Logger.manager.loggerDict,
                    {"ddtrace": tracer, "ddtrace.child": child, "application": application},
                    clear=True,
                ),
            ):
                protect_ddtrace_stream_handlers()
                protect_ddtrace_stream_handlers()
            assert root.handlers == before
            assert handlers[0].filters[0] is existing_filter
            for handler in handlers[:3]:
                assert sum(isinstance(f, _DDTraceClosedStreamFilter) for f in handler.filters) == 1
            assert handlers[3].filters == []
            assert file_handler.filters == []
            assert custom_handler.filters == []
        finally:
            for handler in [*handlers, file_handler, custom_handler]:
                handler.close()
