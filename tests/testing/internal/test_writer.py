"""Tests for ddtrace.testing.internal.writer module."""

import threading
import typing as t
from unittest.mock import Mock
from unittest.mock import call
from unittest.mock import patch

from ddtrace.testing.internal.http import BackendConnectorAgentlessSetup
from ddtrace.testing.internal.http import BackendResult
from ddtrace.testing.internal.test_data import TestModule
from ddtrace.testing.internal.test_data import TestRun
from ddtrace.testing.internal.test_data import TestSession
from ddtrace.testing.internal.test_data import TestStatus
from ddtrace.testing.internal.test_data import TestSuite
from ddtrace.testing.internal.tracer_api import msgpack_packb
from ddtrace.testing.internal.writer import BaseWriter
from ddtrace.testing.internal.writer import Event
from ddtrace.testing.internal.writer import TestCoverageWriter
from ddtrace.testing.internal.writer import TestOptWriter
from ddtrace.testing.internal.writer import _calculate_async_flush_events
from ddtrace.testing.internal.writer import _get_async_flush_events
from ddtrace.testing.internal.writer import _get_min_flush_events
from ddtrace.testing.internal.writer import _truncate_events_meta
from ddtrace.testing.internal.writer import _truncate_payload_metadata
from ddtrace.testing.internal.writer import serialize_module
from ddtrace.testing.internal.writer import serialize_session
from ddtrace.testing.internal.writer import serialize_suite
from ddtrace.testing.internal.writer import serialize_test_run
from tests.testing.mocks import TestDataFactory
from tests.testing.mocks import mock_test_module
from tests.testing.mocks import mock_test_run
from tests.testing.mocks import mock_test_session
from tests.testing.mocks import mock_test_suite


MAX_META_TAG_VALUE_LENGTH = 5000


class _ConcreteWriter(BaseWriter):
    """Minimal concrete subclass for testing BaseWriter."""

    def __init__(
        self,
        min_flush_events: t.Optional[int] = None,
        max_buffer_events: t.Optional[int] = None,
        async_flush_events: t.Optional[int] = None,
        fail_sends: bool = False,
    ) -> None:
        super().__init__(
            min_flush_events=min_flush_events,
            max_buffer_events=max_buffer_events,
            async_flush_events=async_flush_events,
        )
        self.sent_batches: list[list[Event]] = []
        self.sent_event = threading.Event()
        self.fail_sends = fail_sends

    def _send_events(self, events: list[Event]) -> bool:
        self.sent_batches.append(events)
        self.sent_event.set()
        return not self.fail_sends

    def _encode_events(self, events: list[Event]) -> bytes:
        return b"x" * len(events)


class TestBaseWriterAsyncFlushEvents:
    """Tests for BaseWriter.async_flush_events threshold flush."""

    def test_no_async_flush_when_disabled(self) -> None:
        writer = _ConcreteWriter(async_flush_events=None)
        writer.put_event(Event(n=1))
        writer.put_event(Event(n=2))

        assert len(writer.events) == 2
        assert not writer._flush_now.is_set()

    def test_signal_background_flush_on_threshold(self) -> None:
        writer = _ConcreteWriter(async_flush_events=2)
        writer.flush_interval_seconds = 60
        writer.start()
        try:
            writer.put_event(Event(n=1))
            assert writer.sent_batches == []

            writer.put_event(Event(n=2))

            assert writer.sent_event.wait(timeout=5), "background writer did not flush after async threshold"
            assert len(writer.events) == 0
            assert len(writer.sent_batches) == 1
            assert len(writer.sent_batches[0]) == 2
        finally:
            writer.signal_finish()
            writer.wait_finish(timeout=5)

    def test_shutdown_drains_events_added_during_in_flight_flush(self) -> None:
        class _BlockingWriter(_ConcreteWriter):
            def __init__(self) -> None:
                super().__init__(async_flush_events=1)
                self.first_send_started = threading.Event()
                self.unblock_first_send = threading.Event()

            def _send_events(self, events: list[Event]) -> bool:
                self.sent_batches.append(events)
                if len(self.sent_batches) == 1:
                    self.first_send_started.set()
                    self.unblock_first_send.wait(timeout=5)
                self.sent_event.set()
                return True

        writer = _BlockingWriter()
        writer.flush_interval_seconds = 60
        writer.start()

        writer.put_event(Event(n=1))
        assert writer.first_send_started.wait(timeout=5), "first flush did not start"

        writer.put_event(Event(n=2))
        writer.signal_finish()
        writer.unblock_first_send.set()
        writer.wait_finish(timeout=5)

        assert not writer.task.is_alive()
        assert len(writer.events) == 0
        assert [[event["n"] for event in batch] for batch in writer.sent_batches] == [[1], [2]]

    def test_shutdown_immediately_drains_late_events_below_async_threshold(self) -> None:
        class _BlockingWriter(_ConcreteWriter):
            def __init__(self) -> None:
                super().__init__(async_flush_events=100)
                self.first_send_started = threading.Event()
                self.unblock_first_send = threading.Event()

            def _send_events(self, events: list[Event]) -> bool:
                self.sent_batches.append(events)
                if len(self.sent_batches) == 1:
                    self.first_send_started.set()
                    self.unblock_first_send.wait(timeout=5)
                self.sent_event.set()
                return True

        writer = _BlockingWriter()
        writer.flush_interval_seconds = 60
        writer.start()

        writer.put_event(Event(n=1))
        writer.signal_finish()
        assert writer.first_send_started.wait(timeout=5), "first shutdown flush did not start"

        writer.put_event(Event(n=2))
        writer.unblock_first_send.set()
        writer.wait_finish(timeout=5)

        assert not writer.task.is_alive()
        assert len(writer.events) == 0
        assert [[event["n"] for event in batch] for batch in writer.sent_batches] == [[1], [2]]

    def test_set_async_flush_events(self) -> None:
        writer = _ConcreteWriter(async_flush_events=None)
        writer.set_async_flush_events(2)

        assert writer.async_flush_events == 2

    def test_explicit_min_flush_events_stays_synchronous(self) -> None:
        writer = _ConcreteWriter(min_flush_events=1, async_flush_events=100)
        writer.put_event(Event(n=1))

        assert len(writer.events) == 0
        assert writer.sent_batches == [[{"n": 1}]]
        assert not writer._flush_now.is_set()


class TestBaseWriterMaxBufferEvents:
    """Tests for BaseWriter.max_buffer_events bounded buffer."""

    def test_no_limit_when_disabled(self) -> None:
        writer = _ConcreteWriter(max_buffer_events=None)
        for i in range(100):
            writer.put_event(Event(n=i))
        assert len(writer.events) == 100

    def test_drops_events_when_buffer_full(self) -> None:
        writer = _ConcreteWriter(max_buffer_events=3)
        for i in range(10):
            writer.put_event(Event(n=i))
        assert len(writer.events) == 3
        assert writer._dropped_events == 7

    def test_buffer_drains_then_accepts_again(self) -> None:
        writer = _ConcreteWriter(max_buffer_events=2)
        writer.put_event(Event(n=1))
        writer.put_event(Event(n=2))
        writer.put_event(Event(n=3))  # dropped
        assert len(writer.events) == 2
        assert writer._dropped_events == 1

        # Drain the buffer via flush.
        writer.flush()
        assert len(writer.events) == 0

        # Buffer accepts new events again.
        writer.put_event(Event(n=4))
        assert len(writer.events) == 1
        assert writer._dropped_events == 1  # counter not reset


class TestWaitFinishTimeout:
    """Tests for BaseWriter.wait_finish(timeout=...)."""

    def test_wait_finish_with_no_timeout(self) -> None:
        writer = _ConcreteWriter()
        writer.start()
        writer.signal_finish()
        writer.wait_finish()
        assert not writer.task.is_alive()

    def test_wait_finish_respects_timeout(self) -> None:
        """wait_finish returns after the timeout even if the thread is still running."""

        class _SlowWriter(BaseWriter):
            def __init__(self) -> None:
                super().__init__()
                self.started_flushing = threading.Event()
                # Use a *separate* event to block the send so that signal_finish()
                # (which sets should_finish) does not inadvertently unblock _send_events
                # and let the thread exit before the timeout fires.
                self._unblock_send = threading.Event()

            def _send_events(self, events: list[Event]) -> bool:
                self.started_flushing.set()
                # Block until explicitly unblocked (simulates a slow network call).
                self._unblock_send.wait()
                return True

            def _encode_events(self, events: list[Event]) -> bytes:
                return b"x"

        writer = _SlowWriter()
        writer.flush_interval_seconds = 0.01
        writer.start()
        writer.put_event(Event(n=1))
        writer._flush_now.set()
        writer.started_flushing.wait(timeout=5)

        # Thread is stuck in _send_events.  Signal finish so the thread knows it
        # should stop after the current send, but the send itself is still blocked.
        writer.signal_finish()
        writer.wait_finish(timeout=0.1)

        # The timeout must have fired: the thread is still alive because _send_events
        # has not returned yet.
        assert writer.task.is_alive(), "thread should still be running while _send_events is blocked"

        # Unblock the send so the thread can finish cleanly.
        writer._unblock_send.set()
        writer.task.join(timeout=5)


class TestGetAsyncFlushEvents:
    """Tests for _get_async_flush_events env var parsing."""

    def test_default_is_100_without_selected_count(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            import os

            os.environ.pop("_DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS", None)
            os.environ.pop("DD_TRACE_PARTIAL_FLUSH_MIN_SPANS", None)
            assert _get_async_flush_events() == 100

    def test_calculates_from_selected_tests_with_bounds(self) -> None:
        assert _calculate_async_flush_events(100) == 100
        assert _calculate_async_flush_events(4956) == 495
        assert _calculate_async_flush_events(20000) == 1000

    def test_explicit_partial_flush_env_var_uses_sync_flush(self) -> None:
        with patch.dict("os.environ", {"_DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS": "25"}):
            assert _get_min_flush_events() == 25
            assert _get_async_flush_events(selected_tests_count=20000) is None

    def test_reads_legacy_partial_flush_env_var_for_sync_flush(self) -> None:
        with patch.dict("os.environ", {"DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "5"}):
            assert _get_min_flush_events() == 5
            assert _get_async_flush_events() is None

    def test_private_partial_flush_env_var_takes_priority(self) -> None:
        with patch.dict(
            "os.environ",
            {"_DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS": "3", "DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "99"},
        ):
            assert _get_min_flush_events() == 3
            assert _get_async_flush_events() is None

    def test_invalid_explicit_value_disables_threshold_flushing(self) -> None:
        with patch.dict("os.environ", {"_DD_CIVISIBILITY_PARTIAL_FLUSH_MIN_SPANS": "not-a-number"}):
            assert _get_min_flush_events() is None
            assert _get_async_flush_events() is None


class TestEvent:
    """Tests for Event class."""

    def test_event_creation_with_data(self) -> None:
        """Test Event creation with initial data."""
        event = Event(key1="value1", key2="value2")
        assert event["key1"] == "value1"
        assert event["key2"] == "value2"

    def test_event_dict_operations(self) -> None:
        """Test that Event supports dict operations."""
        event = Event()
        event["test"] = "data"
        assert event["test"] == "data"
        assert len(event) == 1


class TestMetadataTruncation:
    def test_payload_metadata_returns_original_when_no_values_truncated(self) -> None:
        metadata = {"*": {"short": "value", "exact": "e" * MAX_META_TAG_VALUE_LENGTH}}

        truncated = _truncate_payload_metadata(metadata)

        assert truncated is metadata
        assert truncated["*"] is metadata["*"]

    def test_payload_metadata_copies_only_when_truncating(self) -> None:
        long_value = "m" * (MAX_META_TAG_VALUE_LENGTH + 1)
        metadata = {"*": {"short": "value", "long": long_value}}

        truncated = _truncate_payload_metadata(metadata)

        assert truncated is not metadata
        assert truncated["*"] is not metadata["*"]
        assert truncated["*"]["short"] == "value"
        assert truncated["*"]["long"] == "m" * MAX_META_TAG_VALUE_LENGTH
        assert metadata["*"]["long"] == long_value

    def test_events_meta_returns_original_when_no_values_truncated(self) -> None:
        event = Event(type="test", content={"meta": {"short": "value"}})
        events = [event]

        truncated = _truncate_events_meta(events)

        assert truncated is events
        assert truncated[0] is event

    def test_events_meta_copies_only_truncated_events(self) -> None:
        long_value = "x" * (MAX_META_TAG_VALUE_LENGTH + 1)
        unchanged_event = Event(type="test", content={"meta": {"short": "value"}})
        truncated_event = Event(type="test", content={"meta": {"long": long_value}})
        events = [unchanged_event, truncated_event]

        truncated = _truncate_events_meta(events)

        assert truncated is not events
        assert truncated[0] is unchanged_event
        assert truncated[1] is not truncated_event
        truncated_content = t.cast(dict[str, t.Any], truncated[1]["content"])
        truncated_meta = t.cast(dict[str, t.Any], truncated_content["meta"])
        original_content = t.cast(dict[str, t.Any], truncated_event["content"])
        original_meta = t.cast(dict[str, t.Any], original_content["meta"])
        assert truncated_meta["long"] == "x" * MAX_META_TAG_VALUE_LENGTH
        assert original_meta["long"] == long_value


class TestTestOptWriter:
    """Tests for TestOptWriter class."""

    @patch("ddtrace.testing.internal.http.BackendConnector")
    def test_testopt_writer_initialization(self, mock_backend_connector: Mock) -> None:
        """Test TestOptWriter initialization."""
        mock_connector = Mock()
        mock_backend_connector.return_value = mock_connector

        writer = TestOptWriter(BackendConnectorAgentlessSetup(site="datadoghq.com", api_key="test_key"))

        assert writer.connector == mock_connector

        # Check metadata structure
        assert "language" in writer.metadata["*"]
        assert writer.metadata["*"]["language"] == "python"
        assert "_dd.origin" in writer.metadata["*"]

        # Check test capabilities
        assert "_dd.library_capabilities.early_flake_detection" in writer.metadata["test"]
        assert "_dd.library_capabilities.coverage_report_upload" in writer.metadata["test"]

        # Check serializers
        assert TestRun in writer.serializers
        assert TestSuite in writer.serializers
        assert TestModule in writer.serializers
        assert TestSession in writer.serializers

    @patch("ddtrace.testing.internal.http.BackendConnector")
    def test_coverage_report_upload_capability_declared(self, mock_backend_connector: Mock) -> None:
        """Test that coverage report upload capability is declared in metadata."""
        mock_connector = Mock()
        mock_backend_connector.return_value = mock_connector

        writer = TestOptWriter(BackendConnectorAgentlessSetup(site="datadoghq.com", api_key="test_key"))

        # Verify the capability is declared with the correct version
        assert writer.metadata["test"]["_dd.library_capabilities.coverage_report_upload"] == "1"

    @patch("ddtrace.testing.internal.http.BackendConnector")
    @patch("ddtrace.testing.internal.writer.msgpack_packb")
    def test_send_events(self, mock_packb: Mock, mock_backend_connector: Mock) -> None:
        """Test sending events to backend."""
        mock_connector = Mock()
        mock_backend_connector.return_value = mock_connector
        mock_connector.request.return_value = BackendResult(
            response=Mock(status=200), response_length=42, elapsed_seconds=1.2
        )
        mock_packb.return_value = b"packed_data"

        writer = TestOptWriter(BackendConnectorAgentlessSetup(site="test", api_key="key"))
        events = [Event(type="test1"), Event(type="test2")]

        writer._send_events(events)

        # Check msgpack packaging
        expected_payload = {
            "version": 1,
            "metadata": writer.metadata,
            "events": events,
        }
        mock_packb.assert_called_once_with(expected_payload)

        # Check HTTP request
        mock_connector.request.assert_called_once_with(
            "POST",
            "/api/v2/citestcycle",
            data=b"packed_data",
            headers={"Content-Type": "application/msgpack"},
            send_gzip=True,
        )

    @patch("ddtrace.testing.internal.http.BackendConnector")
    @patch("ddtrace.testing.internal.writer.msgpack_packb")
    def test_send_events_truncates_payload_metadata_and_event_meta(
        self, mock_packb: Mock, mock_backend_connector: Mock
    ) -> None:
        """Test that test-cycle payload tag values are truncated without mutating originals."""
        mock_connector = Mock()
        mock_backend_connector.return_value = mock_connector
        mock_connector.request.return_value = BackendResult(
            response=Mock(status=200), response_length=42, elapsed_seconds=1.2
        )
        mock_packb.return_value = b"packed_data"
        long_metadata_value = "m" * (MAX_META_TAG_VALUE_LENGTH + 1)
        exact_metadata_value = "e" * MAX_META_TAG_VALUE_LENGTH
        long_event_meta_value = "x" * (MAX_META_TAG_VALUE_LENGTH + 1)
        unicode_event_meta_value = "é" * (MAX_META_TAG_VALUE_LENGTH + 1)

        writer = TestOptWriter(BackendConnectorAgentlessSetup(site="test", api_key="key"))
        writer.metadata["*"]["long_metadata"] = long_metadata_value
        writer.metadata["*"]["exact_metadata"] = exact_metadata_value
        event = Event(
            type="test",
            content={
                "meta": {
                    "long_meta": long_event_meta_value,
                    "unicode_meta": unicode_event_meta_value,
                    "numeric_meta": 123,
                },
                "metrics": {"metric": 1.0},
            },
        )

        writer._send_events([event])

        payload = mock_packb.call_args.args[0]
        metadata = payload["metadata"]["*"]
        meta = payload["events"][0]["content"]["meta"]
        assert metadata["long_metadata"] == "m" * MAX_META_TAG_VALUE_LENGTH
        assert metadata["exact_metadata"] == exact_metadata_value
        assert meta["long_meta"] == "x" * MAX_META_TAG_VALUE_LENGTH
        assert meta["unicode_meta"] == "é" * MAX_META_TAG_VALUE_LENGTH
        assert meta["numeric_meta"] == 123
        assert writer.metadata["*"]["long_metadata"] == long_metadata_value
        event_content = t.cast(dict[str, t.Any], event["content"])
        event_meta = t.cast(dict[str, t.Any], event_content["meta"])
        assert event_meta["long_meta"] == long_event_meta_value

    @patch("ddtrace.testing.internal.http.BackendConnector")
    def test_split_events(self, mock_backend_connector: Mock) -> None:
        """Test sending events to backend."""
        mock_connector = Mock()
        mock_backend_connector.return_value = mock_connector
        mock_connector.request.return_value = BackendResult(
            response=Mock(status=200), response_length=42, elapsed_seconds=1.2
        )

        writer = TestOptWriter(BackendConnectorAgentlessSetup(site="test", api_key="key"))
        writer.metadata = {"*": {"foo": "bar"}}
        writer.max_payload_size = 70

        events = [Event(type="test1"), Event(type="test2"), Event(type="test3")]

        writer._send_events(events)

        expected_pack_1 = msgpack_packb(
            {"version": 1, "metadata": {"*": {"foo": "bar"}}, "events": [{"type": "test1"}]}
        )
        expected_pack_2 = msgpack_packb(
            {"version": 1, "metadata": {"*": {"foo": "bar"}}, "events": [{"type": "test2"}, {"type": "test3"}]}
        )

        # Check HTTP request
        assert mock_connector.request.call_args_list == [
            call(
                "POST",
                "/api/v2/citestcycle",
                data=expected_pack_1,
                headers={"Content-Type": "application/msgpack"},
                send_gzip=True,
            ),
            call(
                "POST",
                "/api/v2/citestcycle",
                data=expected_pack_2,
                headers={"Content-Type": "application/msgpack"},
                send_gzip=True,
            ),
        ]

    @patch("ddtrace.testing.internal.http.BackendConnector")
    def test_connector_closed_after_writer_finishes(self, mock_bc: Mock) -> None:
        """Regression: both the background task and the caller thread must close their
        thread-local connectors.

        ``BackendConnector`` subclasses ``threading.local`` so each thread gets its own
        HTTP connection via ``__init__``. The background thread opens one when the
        periodic task calls ``_send_events``; ``wait_finish()`` also closes the caller
        thread's connector defensively in case that thread ever opened one.

        Without explicit ``close()`` calls on both threads the underlying sockets are
        left open, producing ``ResourceWarning: unclosed socket``.
        """
        close_caller_threads: list[str] = []

        original_connector = Mock()
        original_connector.request.return_value = BackendResult(
            response=Mock(status=200), response_length=0, elapsed_seconds=0.0
        )

        def _close_tracking() -> None:
            close_caller_threads.append(threading.current_thread().name)

        original_connector.close.side_effect = _close_tracking
        mock_bc.return_value = original_connector

        writer = TestOptWriter(BackendConnectorAgentlessSetup(site="test", api_key="key"))
        writer.start()
        writer.put_event(Event(type="test"))
        writer.signal_finish()
        writer.wait_finish()

        # close() must be called at least twice: once from the background task thread
        # and once from the caller (main) thread via wait_finish().
        main_thread_name = threading.main_thread().name
        bg_closes = [t for t in close_caller_threads if t != main_thread_name]
        caller_closes = [t for t in close_caller_threads if t == main_thread_name]
        assert bg_closes, "connector.close() was never called from the background task thread"
        assert caller_closes, "connector.close() was never called from the caller thread (via wait_finish)"


class TestTestCoverageWriter:
    """Tests for TestCoverageWriter class."""

    @patch("ddtrace.testing.internal.http.BackendConnector")
    def test_coverage_writer_initialization(self, mock_backend_connector: Mock) -> None:
        """Test TestCoverageWriter initialization."""
        mock_connector = Mock()
        mock_backend_connector.return_value = mock_connector

        writer = TestCoverageWriter(BackendConnectorAgentlessSetup(site="datadoghq.com", api_key="test_key"))

        assert writer.connector == mock_connector

        # Check connector initialization
        mock_backend_connector.assert_called_once_with(
            url="https://citestcov-intake.datadoghq.com", default_headers={"dd-api-key": "test_key"}, use_gzip=True
        )

    @patch("ddtrace.testing.internal.http.BackendConnector")
    def test_put_coverage(self, mock_backend_connector: Mock) -> None:
        """Test putting coverage data."""
        writer = TestCoverageWriter(BackendConnectorAgentlessSetup(site="test", api_key="key"))

        # Mock test run
        test_run = Mock()
        test_run.session.item_id = 123
        test_run.suite.item_id = 456
        test_run.span_id = 789

        coverage_data: dict[str, bytes] = {
            "file1.py": b"coverage1_bytes",
            "file2.py": b"coverage2_bytes",
        }

        writer.put_coverage(test_run, coverage_data.items())

        # Check event was created and added
        assert len(writer.events) == 1
        event = writer.events[0]
        assert event["test_session_id"] == 123
        assert event["test_suite_id"] == 456
        assert event["span_id"] == 789
        assert len(event["files"]) == 2

    @patch("ddtrace.testing.internal.http.BackendConnector")
    @patch("ddtrace.testing.internal.writer.msgpack_packb")
    def test_send_coverage_events(self, mock_packb: Mock, mock_backend_connector: Mock) -> None:
        """Test sending coverage events."""
        mock_connector = Mock()
        mock_backend_connector.return_value = mock_connector
        mock_connector.post_files.return_value = BackendResult(
            response=Mock(status=200), response_length=42, elapsed_seconds=1.2
        )
        mock_packb.return_value = b"packed_coverage_data"

        writer = TestCoverageWriter(BackendConnectorAgentlessSetup(site="test", api_key="key"))
        events = [Event(type="coverage1"), Event(type="coverage2")]

        writer._send_events(events)

        # Check msgpack packaging
        mock_packb.assert_called_once_with({"version": 2, "coverages": events})

        # Check file attachment structure
        mock_connector.post_files.assert_called_once()
        call_args = mock_connector.post_files.call_args

        assert call_args[0][0] == "/api/v2/citestcov"
        files = call_args[1]["files"]
        assert len(files) == 2
        assert files[0].name == "coverage1"
        assert files[0].content_type == "application/msgpack"
        assert files[1].name == "event"
        assert files[1].content_type == "application/json"
        assert call_args[1]["send_gzip"] is True


class TestSerializationFunctions:
    """Tests for event serialization functions."""

    def create_mock_test_run(self) -> TestRun:
        """Create a mock TestRun with required attributes."""
        test_ref = TestDataFactory.create_test_ref("test_module", "test_suite.py", "test_function")
        test_run = mock_test_run(test_ref)
        test_run.status = TestStatus.PASS
        test_run.start_ns = 1000000000
        test_run.duration_ns = 500000000

        test_run.trace_id = 111
        test_run.span_id = 222
        test_run.service = "test_service"
        test_run.name = "test_function"
        test_run.session.item_id = 333
        test_run.module.item_id = 444
        test_run.suite.item_id = 555
        test_run.tags = {"custom.tag": "value"}
        test_run.metrics = {"custom.metric": 42}

        # Enhance parent hierarchy with additional test-specific attributes
        test_run.parent.tags = {"suite.tag": "suite_value"}
        test_run.parent.parent.name = "TestClass"  # test.suite
        test_run.parent.parent.parent.module_path = "/path/to/test_module.py"

        return test_run

    def test_serialize_test_run_pass(self) -> None:
        """Test serializing a passing test run."""
        test_run = self.create_mock_test_run()

        event = serialize_test_run(test_run)

        assert event["version"] == 2
        assert event["type"] == "test"
        assert event["content"]["trace_id"] == 111
        assert event["content"]["span_id"] == 222
        assert event["content"]["service"] == "test_service"
        assert event["content"]["resource"] == "test_function"
        assert event["content"]["name"] == "pytest.test"
        assert event["content"]["error"] == 0  # Pass = no error
        assert event["content"]["start"] == 1000000000
        assert event["content"]["duration"] == 500000000

        # Check metadata
        meta = event["content"]["meta"]
        assert meta["span.kind"] == "test"
        assert meta["test.name"] == "test_function"
        assert meta["test.status"] == "pass"
        assert meta["test.suite"] == "TestClass"
        assert meta["test.module"] == "test_module"
        assert meta["test.module_path"] == "/path/to/test_module.py"
        assert meta["custom.tag"] == "value"
        assert meta["suite.tag"] == "suite_value"

        # Check metrics
        metrics = event["content"]["metrics"]
        assert metrics["_dd.py.partial_flush"] == 1
        assert metrics["custom.metric"] == 42

    def test_serialize_test_run_fail(self) -> None:
        """Test serializing a failing test run."""
        test_run = self.create_mock_test_run()
        test_run.status = TestStatus.FAIL

        event = serialize_test_run(test_run)

        assert event["content"]["error"] == 1  # Fail = error
        assert event["content"]["meta"]["test.status"] == "fail"

    def create_mock_test_suite(self) -> TestSuite:
        """Create a mock TestSuite."""
        suite_ref = TestDataFactory.create_suite_ref("test_module", "test_suite.py")
        suite = mock_test_suite(suite_ref)
        suite.start_ns = 2000000000
        suite.duration_ns = 1500000000
        suite.status = TestStatus.PASS

        # Add additional test-specific attributes not covered by the builder
        suite.service = "test_service"
        suite.name = "TestSuite"
        suite.session.item_id = 666
        suite.module.item_id = 777
        suite.item_id = 888
        suite.tags = {"suite.custom": "suite_value"}
        suite.metrics = {"suite.metric": 100}

        return suite

    def test_serialize_suite(self) -> None:
        """Test serializing a test suite."""
        suite = self.create_mock_test_suite()

        event = serialize_suite(suite)

        assert event["version"] == 1
        assert event["type"] == "test_suite_end"
        assert event["content"]["service"] == "test_service"
        assert event["content"]["resource"] == "TestSuite"
        assert event["content"]["name"] == "pytest.test_suite"
        assert event["content"]["error"] == 0
        assert event["content"]["start"] == 2000000000
        assert event["content"]["duration"] == 1500000000

        # Check metadata
        meta = event["content"]["meta"]
        assert meta["span.kind"] == "test"
        assert meta["test.suite"] == "TestSuite"
        assert meta["test.status"] == "pass"
        assert meta["type"] == "test_suite_end"
        assert meta["suite.custom"] == "suite_value"

    def create_mock_test_module(self) -> TestModule:
        """Create a mock TestModule."""
        module_ref = TestDataFactory.create_module_ref("test_module")
        module = mock_test_module(module_ref)
        module.status = TestStatus.SKIP
        module.start_ns = 3000000000
        module.duration_ns = 2500000000

        module.service = "test_service"
        module.module_path = "/path/to/test_module.py"
        module.session.item_id = 999
        module.item_id = 1111
        module.tags = {"module.custom": "module_value"}
        module.metrics = {"module.metric": 200}

        return module

    def test_serialize_module(self) -> None:
        """Test serializing a test module."""
        module = self.create_mock_test_module()

        event = serialize_module(module)

        assert event["version"] == 1
        assert event["type"] == "test_module_end"
        assert event["content"]["service"] == "test_service"
        assert event["content"]["resource"] == "test_module"
        assert event["content"]["name"] == "pytest.test_module"
        assert event["content"]["error"] == 0
        assert event["content"]["start"] == 3000000000
        assert event["content"]["duration"] == 2500000000

        # Check metadata
        meta = event["content"]["meta"]
        assert meta["span.kind"] == "test"
        assert meta["test.module"] == "test_module"
        assert meta["test.module_path"] == "/path/to/test_module.py"
        assert meta["test.status"] == "skip"
        assert meta["type"] == "test_module_end"
        assert meta["module.custom"] == "module_value"

    def create_mock_test_session(self) -> TestSession:
        """Create a mock TestSession."""
        session = mock_test_session("test_session")
        session.status = TestStatus.FAIL
        session.start_ns = 4000000000
        session.duration_ns = 3500000000

        session.service = "test_service"
        session.item_id = 2222
        session.tags = {"session.custom": "session_value"}
        session.metrics = {"session.metric": 300}

        return session

    def test_serialize_session(self) -> None:
        """Test serializing a test session."""
        session = self.create_mock_test_session()

        event = serialize_session(session)

        assert event["version"] == 1
        assert event["type"] == "test_session_end"
        assert event["content"]["service"] == "test_service"
        assert event["content"]["resource"] == "test_session"
        assert event["content"]["name"] == "pytest.test_session"
        assert event["content"]["error"] == 0
        assert event["content"]["start"] == 4000000000
        assert event["content"]["duration"] == 3500000000

        # Check metadata
        meta = event["content"]["meta"]
        assert meta["span.kind"] == "test"
        assert meta["test.status"] == "fail"
        assert meta["type"] == "test_session_end"
        assert meta["session.custom"] == "session_value"

        # Check metrics include top level
        metrics = event["content"]["metrics"]
        assert metrics["_dd.top_level"] == 1
        assert metrics["session.metric"] == 300


class _RaisingWriter(BaseWriter):
    """Writer whose _send_events raises an exception."""

    def __init__(self) -> None:
        super().__init__()
        self.send_call_count = 0
        self._send_done = threading.Event()

    def _send_events(self, events: list[Event]) -> bool:
        self.send_call_count += 1
        self._send_done.set()
        raise RuntimeError("serialization boom")

    def _encode_events(self, events: list[Event]) -> bytes:
        return b"x" * len(events)


class TestPeriodicTaskExceptionHandling:
    """Regression tests: an exception in flush() must not kill the daemon thread."""

    def test_thread_survives_send_exception(self) -> None:
        """If _send_events raises, the periodic thread must stay alive and process the finish signal."""
        writer = _RaisingWriter()
        writer.flush_interval_seconds = 60  # long, we'll signal manually

        writer.start()
        # Buffer an event and trigger a flush — _send_events will raise.
        writer.put_event(Event(n=1))
        writer._flush_now.set()
        assert writer._send_done.wait(timeout=5), "flush did not complete"

        # Give the thread time to either crash or recover — without the fix,
        # the unhandled exception kills the thread within this window.
        writer.task.join(timeout=0.5)

        # The thread should still be alive despite the exception.
        assert writer.task.is_alive(), "daemon thread died after _send_events raised"

        # A clean shutdown must complete (not hang).
        writer.signal_finish()
        writer.wait_finish()
        assert not writer.task.is_alive()

    def test_events_after_exception_still_flushed(self) -> None:
        """Events buffered after a crash must be attempted on the next flush cycle."""

        class _FailOnceThenSucceed(BaseWriter):
            def __init__(self) -> None:
                super().__init__()
                self.send_call_count = 0
                self.sent_batches: list[list[Event]] = []
                self._send_done = threading.Event()

            def _send_events(self, events: list[Event]) -> bool:
                self.send_call_count += 1
                if self.send_call_count == 1:
                    self._send_done.set()
                    raise RuntimeError("transient error")
                self.sent_batches.append(events)
                self._send_done.set()
                return True

            def _encode_events(self, events: list[Event]) -> bytes:
                return b"x" * len(events)

        writer = _FailOnceThenSucceed()
        writer.flush_interval_seconds = 60

        writer.start()

        # First flush: will raise.
        writer.put_event(Event(n=1))
        writer._flush_now.set()
        assert writer._send_done.wait(timeout=5), "first flush did not complete"
        writer._send_done.clear()

        # Second flush: should succeed with the new event.
        writer.put_event(Event(n=2))
        writer._flush_now.set()
        assert writer._send_done.wait(timeout=5), "second flush did not complete"

        writer.signal_finish()
        writer.wait_finish()

        assert writer.send_call_count == 2
        assert len(writer.sent_batches) == 1
        assert writer.sent_batches[0] == [{"n": 2}]


class TestCircuitBreaker:
    """Regression tests: after repeated failures, the writer must stop hammering the backend."""

    def test_consecutive_failures_tracked(self) -> None:
        writer = _ConcreteWriter(fail_sends=True)
        for i in range(3):
            writer.put_event(Event(n=i))
            writer.flush()
        assert writer._consecutive_failures == 3

    def test_events_dropped_after_threshold(self) -> None:
        """Once _MAX_CONSECUTIVE_FAILURES is reached, subsequent flushes must drop events."""
        writer = _ConcreteWriter(fail_sends=True)

        # Exhaust the failure threshold (3 failures).
        for i in range(BaseWriter._MAX_CONSECUTIVE_FAILURES):
            writer.put_event(Event(n=i))
            writer.flush()

        assert writer._consecutive_failures == BaseWriter._MAX_CONSECUTIVE_FAILURES
        send_count_before = len(writer.sent_batches)

        # Next flush should drop events without calling _send_events.
        writer.put_event(Event(n=99))
        writer.flush()

        assert len(writer.sent_batches) == send_count_before  # no new send
        assert len(writer.events) == 0  # event was popped and dropped

    def test_probe_after_max_consecutive_failures(self) -> None:
        """Every _MAX_CONSECUTIVE_FAILURES dropped flushes, one probe attempt must be made."""
        writer = _ConcreteWriter(fail_sends=True)
        threshold = BaseWriter._MAX_CONSECUTIVE_FAILURES

        # Reach the threshold.
        for i in range(threshold):
            writer.put_event(Event(n=i))
            writer.flush()

        send_count_at_threshold = len(writer.sent_batches)

        # Next (threshold - 1) flushes should be dropped (no send).
        for i in range(threshold - 1):
            writer.put_event(Event(n=100 + i))
            writer.flush()
        assert len(writer.sent_batches) == send_count_at_threshold

        # The threshold-th flush should probe (call _send_events).
        writer.put_event(Event(n=999))
        writer.flush()
        assert len(writer.sent_batches) == send_count_at_threshold + 1

    def test_recovery_resets_counter(self) -> None:
        """A successful send after failures must reset the counter to 0."""
        writer = _ConcreteWriter(fail_sends=True)

        # Accumulate some failures (but stay below threshold for simplicity).
        writer.put_event(Event(n=1))
        writer.flush()
        writer.put_event(Event(n=2))
        writer.flush()
        assert writer._consecutive_failures == 2

        # Backend recovers.
        writer.fail_sends = False
        writer.put_event(Event(n=3))
        writer.flush()
        assert writer._consecutive_failures == 0

    def test_recovery_after_circuit_open(self) -> None:
        """Full cycle: failure → circuit open → probe succeeds → normal operation resumes."""
        writer = _ConcreteWriter(fail_sends=True)
        threshold = BaseWriter._MAX_CONSECUTIVE_FAILURES

        # Open the circuit.
        for i in range(threshold):
            writer.put_event(Event(n=i))
            writer.flush()
        assert writer._consecutive_failures == threshold

        # Drop (threshold - 1) flushes.
        for i in range(threshold - 1):
            writer.put_event(Event(n=10 + i))
            writer.flush()

        # Backend recovers just before the probe.
        writer.fail_sends = False

        # Probe flush — should succeed and reset.
        writer.put_event(Event(n=99))
        writer.flush()
        assert writer._consecutive_failures == 0

        # Normal operation: next flush should send immediately.
        send_count = len(writer.sent_batches)
        writer.put_event(Event(n=100))
        writer.flush()
        assert len(writer.sent_batches) == send_count + 1

    def test_send_events_return_value_on_backend_error(self) -> None:
        """TestOptWriter._send_events must return False when the backend returns an error."""
        with patch("ddtrace.testing.internal.http.BackendConnector") as mock_bc:
            from ddtrace.testing.internal.telemetry import ErrorType

            mock_connector = Mock()
            mock_bc.return_value = mock_connector
            mock_connector.request.return_value = BackendResult(
                error_type=ErrorType.CODE_5XX,
                error_description="500 Internal Server Error",
                elapsed_seconds=1.0,
            )

            writer = TestOptWriter(BackendConnectorAgentlessSetup(site="test", api_key="key"))
            result = writer._send_events([Event(type="test")])
            assert result is False

    def test_send_events_return_value_on_success(self) -> None:
        """TestOptWriter._send_events must return True on success."""
        with patch("ddtrace.testing.internal.http.BackendConnector") as mock_bc:
            mock_connector = Mock()
            mock_bc.return_value = mock_connector
            mock_connector.request.return_value = BackendResult(
                response=Mock(status=200), response_length=42, elapsed_seconds=1.0
            )

            writer = TestOptWriter(BackendConnectorAgentlessSetup(site="test", api_key="key"))
            result = writer._send_events([Event(type="test")])
            assert result is True


class TestOversizedEventWarning:
    """Regression test: a single event exceeding max_payload_size must log a warning."""

    def test_oversized_single_event_logs_warning(self) -> None:
        writer = _ConcreteWriter()
        writer.max_payload_size = 0  # Force every event to be oversized

        with patch("ddtrace.testing.internal.writer.log") as mock_log:
            packs = writer._split_pack_events([Event(big="data")])

        # Pack is still returned (best-effort send).
        assert len(packs) == 1
        mock_log.warning.assert_called_once()
        assert "exceeds max size" in mock_log.warning.call_args[0][0]

    def test_normal_event_no_warning(self) -> None:
        writer = _ConcreteWriter()
        writer.max_payload_size = 1024 * 1024  # 1 MB — plenty

        with patch("ddtrace.testing.internal.writer.log") as mock_log:
            writer._split_pack_events([Event(n=1)])

        mock_log.warning.assert_not_called()

    def test_splittable_events_no_warning(self) -> None:
        """Multiple events that individually fit should split cleanly, no warning."""
        writer = _ConcreteWriter()
        writer.max_payload_size = 2  # Each event encodes to 1 byte

        with patch("ddtrace.testing.internal.writer.log") as mock_log:
            writer._split_pack_events([Event(n=1), Event(n=2), Event(n=3)])

        mock_log.warning.assert_not_called()
