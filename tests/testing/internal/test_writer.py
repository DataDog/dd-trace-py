"""Tests for ddtrace.testing.internal.writer module."""

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
from ddtrace.testing.internal.writer import Event
from ddtrace.testing.internal.writer import TestCoverageWriter
from ddtrace.testing.internal.writer import TestOptWriter
from ddtrace.testing.internal.writer import serialize_module
from ddtrace.testing.internal.writer import serialize_session
from ddtrace.testing.internal.writer import serialize_suite
from ddtrace.testing.internal.writer import serialize_test_run
from tests.testing.mocks import TestDataFactory
from tests.testing.mocks import mock_test_module
from tests.testing.mocks import mock_test_run
from tests.testing.mocks import mock_test_session
from tests.testing.mocks import mock_test_suite


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

        # Check serializers
        assert TestRun in writer.serializers
        assert TestSuite in writer.serializers
        assert TestModule in writer.serializers
        assert TestSession in writer.serializers

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
