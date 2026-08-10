"""Tests for ddtrace.testing.logs (public DDTestLogsHandler API)."""

from __future__ import annotations

import logging
import threading
from typing import Optional
from unittest.mock import Mock
from unittest.mock import patch

import pytest

from ddtrace.testing.internal.errors import SetupError
from ddtrace.testing.logs import CorrelationFilter
from ddtrace.testing.logs import DDTestLogsHandler
from ddtrace.testing.logs import ThreadLocalCorrelationFilter


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

    def test_close_waits_for_in_flight_emit_under_handler_lock(self) -> None:
        """close() must acquire the handler lock so it cannot race with a thread mid-emit().

        logging.Handler.handle() runs emit() under self.lock.  If close() flipped
        _closed and drained the writer without that lock, a thread already inside
        emit() (past the _closed check) could still call put_event() after the
        writer thread had exited, silently losing records.
        """
        handler, mock_writer = _make_handler()

        # Simulate a thread mid-emit by holding the handler lock from this thread.
        handler.acquire()

        close_returned = threading.Event()
        signal_finish_called_before_release: list[bool] = []
        # Track whether signal_finish() ran before we released the lock; if close()
        # is correctly serialized, it must not have.
        lock_released = threading.Event()

        def record_call(*_args, **_kwargs):
            signal_finish_called_before_release.append(not lock_released.is_set())

        mock_writer.signal_finish.side_effect = record_call

        def closer() -> None:
            handler.close()
            close_returned.set()

        closer_thread = threading.Thread(target=closer)
        closer_thread.start()

        # Give the closer a moment to reach handler.acquire() and block on it.
        # If close() is not properly serialized, it will race past acquire() and
        # signal_finish() will run while we still hold the lock.
        close_returned.wait(timeout=0.2)
        assert not close_returned.is_set(), "close() did not block on the handler lock"
        mock_writer.signal_finish.assert_not_called()

        # Release the lock — close() should now proceed.
        lock_released.set()
        handler.release()

        close_returned.wait(timeout=2.0)
        assert close_returned.is_set(), "close() did not return after lock release"
        closer_thread.join(timeout=2.0)

        mock_writer.signal_finish.assert_called_once()
        # signal_finish() must have been observed after we released the lock.
        assert signal_finish_called_before_release == [False]


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


def _make_record(msg: str = "msg") -> logging.LogRecord:
    return logging.LogRecord("test", logging.WARNING, "", 0, msg, (), None)


class TestCorrelationFilterBase:
    def test_base_class_get_methods_raise_not_implemented(self) -> None:
        filt = CorrelationFilter()
        with pytest.raises(NotImplementedError):
            filt.get_trace_id()
        with pytest.raises(NotImplementedError):
            filt.get_span_id()

    def test_subclass_writes_dd_attrs_onto_record(self) -> None:
        class Static(CorrelationFilter):
            def get_trace_id(self) -> Optional[str]:
                return "abc"

            def get_span_id(self) -> Optional[str]:
                return "def"

        record = _make_record()
        assert Static().filter(record) is True
        assert getattr(record, "dd.trace_id") == "abc"
        assert getattr(record, "dd.span_id") == "def"

    def test_none_from_subclass_defaults_to_zero(self) -> None:
        class Empty(CorrelationFilter):
            def get_trace_id(self) -> Optional[str]:
                return None

            def get_span_id(self) -> Optional[str]:
                return None

        record = _make_record()
        Empty().filter(record)
        assert getattr(record, "dd.trace_id") == "0"
        assert getattr(record, "dd.span_id") == "0"

    def test_empty_string_from_subclass_defaults_to_zero(self) -> None:
        """Falsy values (not just None) fall back to the zero sentinel."""

        class Blank(CorrelationFilter):
            def get_trace_id(self) -> Optional[str]:
                return ""

            def get_span_id(self) -> Optional[str]:
                return ""

        record = _make_record()
        Blank().filter(record)
        assert getattr(record, "dd.trace_id") == "0"
        assert getattr(record, "dd.span_id") == "0"

    def test_filter_always_returns_true(self) -> None:
        """The filter never drops records — it only stamps attributes."""

        class Empty(CorrelationFilter):
            def get_trace_id(self) -> Optional[str]:
                return None

            def get_span_id(self) -> Optional[str]:
                return None

        assert Empty().filter(_make_record()) is True

    def test_subclass_with_arbitrary_setter_signature(self) -> None:
        """The base does not constrain how subclasses accept new context."""

        class GlobalFilter(CorrelationFilter):
            def __init__(self) -> None:
                super().__init__()
                self._ids = (None, None)

            def update(self, ids: tuple) -> None:  # any signature works
                self._ids = ids

            def get_trace_id(self) -> Optional[str]:
                return self._ids[0]

            def get_span_id(self) -> Optional[str]:
                return self._ids[1]

        filt = GlobalFilter()
        filt.update(("t", "s"))
        record = _make_record()
        filt.filter(record)
        assert getattr(record, "dd.trace_id") == "t"
        assert getattr(record, "dd.span_id") == "s"


class TestThreadLocalCorrelationFilter:
    def test_defaults_to_zero_before_set_context(self) -> None:
        filt = ThreadLocalCorrelationFilter()
        record = _make_record()
        filt.filter(record)
        assert getattr(record, "dd.trace_id") == "0"
        assert getattr(record, "dd.span_id") == "0"

    def test_set_context_stamps_record(self) -> None:
        filt = ThreadLocalCorrelationFilter()
        filt.set_context(trace_id="111", span_id="222")
        record = _make_record()
        filt.filter(record)
        assert getattr(record, "dd.trace_id") == "111"
        assert getattr(record, "dd.span_id") == "222"

    def test_clear_context_resets_to_zero(self) -> None:
        filt = ThreadLocalCorrelationFilter()
        filt.set_context(trace_id="111", span_id="222")
        filt.clear_context()
        record = _make_record()
        filt.filter(record)
        assert getattr(record, "dd.trace_id") == "0"
        assert getattr(record, "dd.span_id") == "0"

    def test_clear_context_is_safe_when_never_set(self) -> None:
        filt = ThreadLocalCorrelationFilter()
        filt.clear_context()  # must not raise
        record = _make_record()
        filt.filter(record)
        assert getattr(record, "dd.trace_id") == "0"

    def test_threads_have_independent_context(self) -> None:
        filt = ThreadLocalCorrelationFilter()
        filt.set_context(trace_id="main-t", span_id="main-s")

        seen: dict[str, tuple] = {}
        started = threading.Event()
        proceed = threading.Event()

        def worker(name: str, trace_id: str, span_id: str) -> None:
            # Each worker sets its own context, then reads back via filter().
            filt.set_context(trace_id=trace_id, span_id=span_id)
            started.set()
            proceed.wait(timeout=2.0)
            record = _make_record()
            filt.filter(record)
            seen[name] = (getattr(record, "dd.trace_id"), getattr(record, "dd.span_id"))

        t1 = threading.Thread(target=worker, args=("a", "a-t", "a-s"))
        t2 = threading.Thread(target=worker, args=("b", "b-t", "b-s"))
        t1.start()
        t2.start()

        # Confirm the main thread still sees its own context while workers are alive.
        started.wait(timeout=2.0)
        main_record = _make_record()
        filt.filter(main_record)
        assert getattr(main_record, "dd.trace_id") == "main-t"
        assert getattr(main_record, "dd.span_id") == "main-s"

        proceed.set()
        t1.join(timeout=2.0)
        t2.join(timeout=2.0)

        assert seen["a"] == ("a-t", "a-s")
        assert seen["b"] == ("b-t", "b-s")

    def test_set_context_overwrites_previous_values(self) -> None:
        filt = ThreadLocalCorrelationFilter()
        filt.set_context(trace_id="first-t", span_id="first-s")
        filt.set_context(trace_id="second-t", span_id="second-s")
        record = _make_record()
        filt.filter(record)
        assert getattr(record, "dd.trace_id") == "second-t"
        assert getattr(record, "dd.span_id") == "second-s"


class TestCorrelationFilterWithHandler:
    """End-to-end-ish: the filter feeds the handler the attributes it reads in emit()."""

    def test_filter_stamps_record_that_handler_then_reads(self) -> None:
        mock_writer = Mock()
        mock_writer.hostname = "h"
        mock_writer.service = "svc"

        with patch("ddtrace.testing.logs.BackendConnectorSetup.detect_standard_setup"):
            with patch("ddtrace.testing.logs.LogsWriter", return_value=mock_writer):
                handler = DDTestLogsHandler(service="svc")

        correlation = ThreadLocalCorrelationFilter()
        correlation.set_context(trace_id="trace-xyz", span_id="span-xyz")
        handler.addFilter(correlation)

        logger = logging.getLogger("ddtrace.testing.logs.test_correlation_handler")
        logger.setLevel(logging.DEBUG)
        # Calling handler.handle() (not emit() directly) ensures the filter runs first,
        # matching what stdlib logging does in production.
        record = _make_record("hello")
        handler.handle(record)

        event = mock_writer.put_event.call_args[0][0]
        assert event["dd.trace_id"] == "trace-xyz"
        assert event["dd.span_id"] == "span-xyz"
