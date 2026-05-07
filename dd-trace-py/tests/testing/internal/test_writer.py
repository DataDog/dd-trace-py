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
from ddtrace.testing.internal.writer import _get_min_flush_events
from ddtrace.testing.internal.writer import serialize_module
from ddtrace.testing.internal.writer import serialize_session
from ddtrace.testing.internal.writer import serialize_suite
from ddtrace.testing.internal.writer import serialize_test_run
from tests.testing.mocks import TestDataFactory
from tests.testing.mocks import mock_test_module
from tests.testing.mocks import mock_test_run
from tests.testing.mocks import mock_test_session
from tests.testing.mocks import mock_test_suite


class _ConcreteWriter(BaseWriter):
    """Minimal concrete subclass for testing BaseWriter."""

    def __init__(
        self,
        min_flush_events: t.Optional[int] = None,
        max_buffer_events: t.Optional[int] = None,
        fail_sends: bool = False,
    ) -> None:
        super().__init__(min_flush_events=min_flush_events, max_buffer_events=max_buffer_events)
        self.sent_batches: list[list[Event]] = []
        self.fail_sends = fail_sends

    def _send_events(self, events: list[Event]) -> bool:
        self.sent_batches.append(events)
        return not self.fail_sends

    def _encode_events(self, events: list[Event]) -> bytes:
        return b"x" * len(events)


class TestBaseWriterMinFlushEvents:
    """Tests for BaseWriter.min_flush_events threshold flush."""

    def test_no_flush_when_disabled(self) -> None:
        writer = _ConcreteWriter(min_flush_events=None)
        writer.put_event(Event(n=1))
        writer.put_event(Event(n=2))

        assert len(writer.events) == 2
        assert writer.sent_batches == []

    def test_flush_on_every_event_with_threshold_1(self) -> None:
        writer = _ConcreteWriter(min_flush_events=1)
        writer.put_event(Event(n=1))

        assert len(writer.events) == 0
        assert len(writer.sent_batches) == 1
        assert writer.sent_batches[0] == [{"n": 1}]

    def test_flush_on_threshold_3(self) -> None:
        writer = _ConcreteWriter(min_flush_events=3)
        writer.put_event(Event(n=1))
        writer.put_event(Event(n=2))
        assert len(writer.events) == 2
        assert writer.sent_batches == []

        writer.put_event(Event(n=3))
        assert len(writer.events) == 0
        assert len(writer.sent_batches) == 1
        assert len(writer.sent_batches[0]) == 3

    def test_multiple_flush_cycles(self) -> None:
        writer = _ConcreteWriter(min_flush_events=2)
        writer.put_event(Event(n=1))
        writer.put_event(Event(n=2))
        assert len(writer.sent_batches) == 1

        writer.put_event(Event(n=3))
        writer.put_event(Event(n=4))
        assert len(writer.sent_batches) == 2


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

    def test_max_buffer_events_compatible_with_min_flush(self) -> None:
        """Both min_flush_events and max_buffer_events can coexist."""
        writer = _ConcreteWriter(min_flush_events=2, max_buffer_events=5)
        writer.put_event(Event(n=1))
        writer.put_event(Event(n=2))
        # min_flush_events triggers a sync flush after 2 events.
        assert len(writer.sent_batches) == 1
        assert len(writer.events) == 0


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

            def _send_events(self, events: list[Event]) -> bool:
                self.started_flushing.set()
                # Block until finish is signalled (simulates slow network).
                self.should_finish.wait()
                return True

            def _encode_events(self, events: list[Event]) -> bytes:
                return b"x"

        writer = _SlowWriter()
        writer.flush_interval_seconds = 0.01
        writer.start()
        writer.put_event(Event(n=1))
        writer._flush_now.set()
        writer.started_flushing.wait(timeout=5)

        # With a short timeout, wait_finish should return even though the thread is stuck.
        writer.signal_finish()
        writer.wait_finish(timeout=0.1)
        # Thread may still be alive because we timed out.
        # Clean up: signal the thread to finish.
        writer.should_finish.set()
        writer.task.join(timeout=5)


class TestGetMinFlushEvents:
    """Tests for _get_min_flush_events env var parsing."""

    def test_default_is_none(self) -> None:
        with patch.dict("os.environ", {}, clear=False):
            # Remove the var if it exists
            import os

            os.environ.pop("DD_TRACE_PARTIAL_FLUSH_MIN_SPANS", None)
            assert _get_min_flush_events() is None

    def test_reads_env_var(self) -> None:
        with patch.dict("os.environ", {"DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "5"}):
            assert _get_min_flush_events() == 5

    def test_value_1(self) -> None:
        with patch.dict("os.environ", {"DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "1"}):
            assert _get_min_flush_events() == 1

    def test_negative_returns_none(self) -> None:
        with patch.dict("os.environ", {"DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "-3"}):
            assert _get_min_flush_events() is None

    def test_zero_returns_none(self) -> None:
        with patch.dict("os.environ", {"DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "0"}):
            assert _get_min_flush_events() is None

    def test_invalid_value_returns_none(self) -> None:
        with patch.dict("os.environ", {"DD_TRACE_PARTIAL_FLUSH_MIN_SPANS": "not_a_number"}):
            assert _get_min_flush_events() is None


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
