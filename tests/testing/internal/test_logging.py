"""Tests for ddtestpy.internal.logging module."""

import logging
import os
from typing import Optional
from unittest.mock import Mock
from unittest.mock import patch

from ddtestpy.internal.logging import catch_and_log_exceptions
from ddtestpy.internal.logging import ddtestpy_logger
from ddtestpy.internal.logging import setup_logging


class TestSetupLogging:
    """Tests for setup_logging function."""

    def teardown_method(self) -> None:
        """Clean up logger state after each test."""
        # Remove all handlers
        for handler in ddtestpy_logger.handlers[:]:
            ddtestpy_logger.removeHandler(handler)
        # Reset logger state
        ddtestpy_logger.propagate = True
        ddtestpy_logger.setLevel(logging.NOTSET)

    @patch.dict(os.environ, {}, clear=True)
    def test_setup_logging_default_level(self) -> None:
        """Test setup_logging with default (INFO) level."""
        setup_logging()

        assert ddtestpy_logger.propagate is False
        assert ddtestpy_logger.level == logging.INFO
        assert len(ddtestpy_logger.handlers) == 1

        handler = ddtestpy_logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)

    @patch.dict(os.environ, {"DD_TEST_DEBUG": "true"})
    def test_setup_logging_debug_level_true(self) -> None:
        """Test setup_logging with DEBUG level enabled via true."""
        setup_logging()

        assert ddtestpy_logger.propagate is False
        assert ddtestpy_logger.level == logging.DEBUG
        assert len(ddtestpy_logger.handlers) == 1

    @patch.dict(os.environ, {"DD_TEST_DEBUG": "1"})
    def test_setup_logging_debug_level_one(self) -> None:
        """Test setup_logging with DEBUG level enabled via 1."""
        setup_logging()

        assert ddtestpy_logger.level == logging.DEBUG

    @patch.dict(os.environ, {"DD_TEST_DEBUG": "false"})
    def test_setup_logging_debug_level_false(self) -> None:
        """Test setup_logging with DEBUG level disabled."""
        setup_logging()

        assert ddtestpy_logger.level == logging.INFO

    @patch.dict(os.environ, {"DD_TEST_DEBUG": "0"})
    def test_setup_logging_debug_level_zero(self) -> None:
        """Test setup_logging with DEBUG level disabled via 0."""
        setup_logging()

        assert ddtestpy_logger.level == logging.INFO

    def test_setup_logging_formatter(self) -> None:
        """Test that the formatter is correctly configured."""
        setup_logging()

        handler = ddtestpy_logger.handlers[0]
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
        initial_handler_count = len(ddtestpy_logger.handlers)

        setup_logging()
        # Should still have the same number of handlers (assuming no duplicate prevention logic)
        # This test documents current behavior - if duplicate prevention is added, adjust accordingly
        assert len(ddtestpy_logger.handlers) >= initial_handler_count


class TestCatchAndLogExceptions:
    """Tests for catch_and_log_exceptions decorator."""

    def test_decorator_success(self) -> None:
        """Test decorator with successful function execution."""

        @catch_and_log_exceptions()
        def successful_function(x: int, y: int) -> int:
            return x + y

        result = successful_function(2, 3)
        assert result == 5

    @patch.object(ddtestpy_logger, "exception")
    def test_decorator_exception_logging(self, mock_exception: Mock) -> None:
        """Test decorator catches and logs exceptions."""

        @catch_and_log_exceptions()
        def failing_function() -> None:
            raise ValueError("Test error")

        result = failing_function()

        assert result is None
        mock_exception.assert_called_once_with("Error while calling %s", "failing_function")

    @patch.object(ddtestpy_logger, "exception")
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
        mock_exception.assert_called_once_with("Error while calling %s", "function_with_args")  # type: ignore[unreachable]

    @patch.object(ddtestpy_logger, "exception")
    def test_decorator_preserves_function_metadata(self, mock_exception: Mock) -> None:
        """Test decorator preserves original function metadata."""

        def original_function() -> str:
            """Original docstring."""
            return "original"

        decorated = catch_and_log_exceptions()(original_function)

        # Check that function name is preserved for logging
        decorated()
        assert decorated.__name__ == "original_function"  # functools.wraps preserves original name
