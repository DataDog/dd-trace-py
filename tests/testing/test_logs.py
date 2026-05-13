"""Tests for ddtrace.testing.logs (public DDTestLogsHandler API)."""

from __future__ import annotations

import logging
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.errors import SetupError
from ddtrace.testing.logs import DDTestLogsHandler


def _make_handler(service: str = "test-service", shutdown_timeout: float = 5.0) -> tuple[DDTestLogsHandler, Mock]:
    """Create a DDTestLogsHandler with mocked internals. Returns (handler, mock_writer)."""
    mock_writer = Mock()
    mock_writer.hostname = "test-host"
    mock_writer.service = service

    with patch("ddtrace.testing.logs.BackendConnectorSetup.detect_standard_setup"):
        with patch("ddtrace.testing.logs.LogsWriter", return_value=mock_writer):
            handler = DDTestLogsHandler(service=service, shutdown_timeout=shutdown_timeout)

    return handler, mock_writer


class TestDDTestLogsHandlerInit:
    def test_uses_detect_standard_setup_not_detect_setup(self) -> None:
        """Handler must use the public detection path, not the internal one with _CI_DD_* vars."""
        mock_writer = Mock()
        mock_writer.hostname = "h"
        mock_writer.service = "s"

        with patch("ddtrace.testing.logs.BackendConnectorSetup.detect_standard_setup") as mock_standard:
            with patch("ddtrace.testing.logs.BackendConnectorSetup.detect_setup") as mock_internal:
                with patch("ddtrace.testing.logs.LogsWriter", return_value=mock_writer):
                    DDTestLogsHandler(service="svc")

        mock_standard.assert_called_once()
        mock_internal.assert_not_called()

    def test_writer_is_started_at_init(self) -> None:
        _, mock_writer = _make_handler()
        mock_writer.start.assert_called_once()

    def test_setup_error_propagates(self) -> None:
        with patch(
            "ddtrace.testing.logs.BackendConnectorSetup.detect_standard_setup",
            side_effect=SetupError("DD_API_KEY environment variable is not set"),
        ):
            with pytest.raises(SetupError, match="DD_API_KEY"):
                DDTestLogsHandler(service="svc")

    def test_default_shutdown_timeout(self) -> None:
        handler, mock_writer = _make_handler()
        handler.close()
        mock_writer.wait_finish.assert_called_once_with(timeout=5.0)

    def test_custom_shutdown_timeout(self) -> None:
        handler, mock_writer = _make_handler(shutdown_timeout=2.5)
        handler.close()
        mock_writer.wait_finish.assert_called_once_with(timeout=2.5)


class TestDDTestLogsHandlerClose:
    def test_close_signals_and_waits(self) -> None:
        handler, mock_writer = _make_handler()
        handler.close()
        mock_writer.signal_finish.assert_called_once()
        mock_writer.wait_finish.assert_called_once_with(timeout=5.0)

    def test_emit_is_no_op_after_close(self) -> None:
        """Records emitted after close() must not reach the writer (they would queue but never flush)."""
        handler, mock_writer = _make_handler()
        handler.close()
        mock_writer.reset_mock()

        record = logging.LogRecord("test", logging.WARNING, "", 0, "late record", (), None)
        handler.emit(record)

        mock_writer.put_event.assert_not_called()

    def test_close_gates_emits_before_signalling_writer(self) -> None:
        """_closed must be True before signal_finish() is called so no record can sneak into a draining writer."""
        handler, mock_writer = _make_handler()
        closed_at_signal_time: list[bool] = []

        def capture_closed(*_args, **_kwargs):
            closed_at_signal_time.append(handler._closed)

        mock_writer.signal_finish.side_effect = capture_closed
        handler.close()

        assert closed_at_signal_time == [True]


class TestDDTestLogsHandlerContextManager:
    def test_enter_returns_handler(self) -> None:
        handler, _ = _make_handler()
        assert handler.__enter__() is handler

    def test_exit_calls_close(self) -> None:
        handler, mock_writer = _make_handler()
        handler.__exit__(None, None, None)
        mock_writer.signal_finish.assert_called_once()

    def test_close_called_on_exception(self) -> None:
        handler, mock_writer = _make_handler()
        try:
            with handler:
                raise ValueError("boom")
        except ValueError:
            pass
        mock_writer.signal_finish.assert_called_once()

    def test_context_manager_full_lifecycle(self) -> None:
        mock_writer = Mock()
        mock_writer.hostname = "h"
        mock_writer.service = "s"

        with patch("ddtrace.testing.logs.BackendConnectorSetup.detect_standard_setup"):
            with patch("ddtrace.testing.logs.LogsWriter", return_value=mock_writer):
                with DDTestLogsHandler(service="svc"):
                    pass

        mock_writer.signal_finish.assert_called_once()
        mock_writer.wait_finish.assert_called_once()


class TestDDTestLogsHandlerEmit:
    def test_emit_puts_event_to_writer(self) -> None:
        handler, mock_writer = _make_handler()
        record = logging.LogRecord("test", logging.WARNING, "", 0, "hello world", (), None)
        setattr(record, "dd.trace_id", "123")
        setattr(record, "dd.span_id", "456")

        handler.emit(record)

        mock_writer.put_event.assert_called_once()
        event = mock_writer.put_event.call_args[0][0]
        assert event["message"] == "hello world"
        assert event["dd.trace_id"] == "123"
        assert event["dd.span_id"] == "456"
        assert event["service"] == "test-service"
        assert event["ddsource"] == "python"
        assert event["ddtags"] == "datadog.product:citest"

    def test_emit_missing_correlation_ids_defaults_to_zero(self) -> None:
        handler, mock_writer = _make_handler()
        record = logging.LogRecord("test", logging.WARNING, "", 0, "msg", (), None)
        # No dd.trace_id / dd.span_id on record

        handler.emit(record)

        event = mock_writer.put_event.call_args[0][0]
        assert event["dd.trace_id"] == "0"
        assert event["dd.span_id"] == "0"
