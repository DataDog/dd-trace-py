"""Improved mock utilities for test optimization framework testing.

This module provides flexible and easy-to-use mock builders and utilities
for testing the ddtrace.testing framework. The design emphasizes:
- Builder pattern for flexible mock construction
- Centralized default configurations
- Simplified session manager creation
- Utility functions for common patterns
"""

from __future__ import annotations

import contextlib
import os
from pathlib import Path
import typing as t
from unittest.mock import Mock
from unittest.mock import patch

from _pytest.reports import TestReport

from ddtrace.testing.internal.http import BackendConnectorSetup
from ddtrace.testing.internal.http import BackendResult
from ddtrace.testing.internal.http import ErrorType
from ddtrace.testing.internal.session_manager import SessionManager
from ddtrace.testing.internal.settings_data import AutoTestRetriesSettings
from ddtrace.testing.internal.settings_data import EarlyFlakeDetectionSettings
from ddtrace.testing.internal.settings_data import Settings
from ddtrace.testing.internal.settings_data import TestManagementSettings
from ddtrace.testing.internal.settings_data import TestProperties
from ddtrace.testing.internal.test_data import ModuleRef
from ddtrace.testing.internal.test_data import SuiteRef
from ddtrace.testing.internal.test_data import Test
from ddtrace.testing.internal.test_data import TestModule
from ddtrace.testing.internal.test_data import TestRef
from ddtrace.testing.internal.test_data import TestRun
from ddtrace.testing.internal.test_data import TestSession
from ddtrace.testing.internal.test_data import TestSuite
from ddtrace.testing.internal.writer import Event
from ddtrace.testing.internal.writer import TestOptWriter


def get_mock_git_instance() -> Mock:
    mock_git_instance = Mock()
    mock_git_instance.get_latest_commits.return_value = []
    mock_git_instance.get_filtered_revisions.return_value = []
    mock_git_instance.pack_objects.return_value = iter([])
    return mock_git_instance


class MockDefaults:
    """Centralized default configurations for mocks."""

    @staticmethod
    def settings(
        skipping_enabled: bool = True,
        early_flake_detection: bool = False,
        test_management: bool = False,
        auto_test_retries: bool = False,
        known_tests_enabled: bool = False,
        coverage_enabled: bool = False,
        require_git: bool = False,
        itr_enabled: bool = False,
    ) -> Settings:
        """Create default Settings object."""
        return Settings(
            early_flake_detection=EarlyFlakeDetectionSettings(enabled=early_flake_detection),
            test_management=TestManagementSettings(enabled=test_management),
            auto_test_retries=AutoTestRetriesSettings(enabled=auto_test_retries),
            known_tests_enabled=known_tests_enabled,
            coverage_enabled=coverage_enabled,
            skipping_enabled=skipping_enabled,
            require_git=require_git,
            itr_enabled=itr_enabled,
        )

    @staticmethod
    def test_environment() -> t.Dict[str, str]:
        """Create default test environment variables."""
        return {
            "DD_API_KEY": "test-api-key",
            "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true",
            "DD_SERVICE": "test-service",
            "DD_ENV": "test-env",
        }

    @staticmethod
    def test_session(name: str = "test") -> TestSession:
        """Create default test session."""
        session = TestSession(name=name)
        session.set_attributes(test_command="pytest", test_framework="pytest", test_framework_version="1.0.0")
        return session


# =============================================================================
# MOCK BUILDERS
# =============================================================================


class SessionManagerMockBuilder:
    """Builder for creating SessionManager mocks with flexible configuration."""

    def __init__(self) -> None:
        self._settings = MockDefaults.settings()
        self._skippable_items: t.Set[t.Union[TestRef, SuiteRef]] = set()
        self._test_properties: t.Dict[TestRef, TestProperties] = {}
        self._known_tests: t.Set[TestRef] = set()
        self._known_commits: t.List[str] = []
        self._workspace_path = "/fake/workspace"
        self._retry_handlers: t.List[Mock] = []
        self._env_tags: t.Dict[str, str] = {}

    def with_settings(self, settings: Settings) -> "SessionManagerMockBuilder":
        """Set custom settings."""
        self._settings = settings
        return self

    def with_skipping_enabled(self, enabled: bool) -> "SessionManagerMockBuilder":
        """Enable or disable test skipping."""
        self._settings = Settings(
            early_flake_detection=self._settings.early_flake_detection,
            test_management=self._settings.test_management,
            auto_test_retries=self._settings.auto_test_retries,
            known_tests_enabled=self._settings.known_tests_enabled,
            coverage_enabled=enabled,
            skipping_enabled=enabled,
            require_git=self._settings.require_git,
            itr_enabled=self._settings.itr_enabled,
        )
        return self

    def with_skippable_items(self, items: t.Set[t.Union[TestRef, SuiteRef]]) -> "SessionManagerMockBuilder":
        """Set skippable test/suite items."""
        self._skippable_items = items
        return self

    def with_test_properties(self, properties: t.Dict[TestRef, TestProperties]) -> "SessionManagerMockBuilder":
        """Set test properties."""
        self._test_properties = properties
        return self

    def with_known_tests(self, tests: t.Set[TestRef]) -> "SessionManagerMockBuilder":
        """Set known tests."""
        self._known_tests = tests
        return self

    def with_workspace_path(self, path: str) -> "SessionManagerMockBuilder":
        """Set workspace path."""
        self._workspace_path = path
        return self

    def with_env_tags(self, tags: t.Dict[str, str]) -> "SessionManagerMockBuilder":
        """Set tags extracted from environment."""
        self._env_tags = tags
        return self

    def build_mock(self) -> Mock:
        """Build a Mock SessionManager object."""
        mock_manager = Mock(spec=SessionManager)

        # Configure basic attributes
        mock_manager.settings = self._settings
        mock_manager.skippable_items = self._skippable_items
        mock_manager.test_properties = self._test_properties
        mock_manager.workspace_path = self._workspace_path
        mock_manager.retry_handlers = self._retry_handlers
        mock_manager.env_tags = self._env_tags

        mock_manager.session = Mock()
        mock_manager.writer = Mock()
        mock_manager.coverage_writer = Mock()
        mock_manager.telemetry_api = Mock()

        return mock_manager

    def build_real_with_mocks(self, test_env: t.Optional[t.Dict[str, str]] = None) -> SessionManager:
        """Build a real SessionManager with mocked dependencies.

        NOTE: This creates the SessionManager with mocked dependencies during initialization.
        The mocks are cleaned up after creation, so this assumes SessionManager doesn't
        make further API calls after __init__.
        """
        if test_env is None:
            test_env = MockDefaults.test_environment()

        with patch("ddtrace.testing.internal.session_manager.APIClient") as mock_api_client:
            # Configure API client mock
            mock_client = Mock()
            mock_client.get_settings.return_value = self._settings
            mock_client.get_known_tests.return_value = self._known_tests
            mock_client.get_test_management_properties.return_value = self._test_properties
            mock_client.get_known_commits.return_value = self._known_commits
            mock_client.send_git_pack_file.return_value = None
            mock_client.get_skippable_tests.return_value = (self._skippable_items, None)
            mock_api_client.return_value = mock_client

            with (
                patch("ddtrace.testing.internal.session_manager.get_env_tags", return_value=self._env_tags),
                patch("ddtrace.testing.internal.session_manager.get_platform_tags", return_value={}),
                patch("ddtrace.testing.internal.session_manager.Git", return_value=get_mock_git_instance()),
                patch.dict(os.environ, test_env),
            ):
                # Create session manager
                test_session = MockDefaults.test_session()
                session_manager = SessionManager(session=test_session)
                session_manager.skippable_items = self._skippable_items

                return session_manager


class TestMockBuilder:
    """Builder for creating Test mocks with flexible configuration."""

    def __init__(self, test_ref: TestRef):
        self._test_ref = test_ref
        self._is_attempt_to_fix = False
        self._is_disabled = False
        self._is_quarantined = False
        self._test_runs: t.List[Mock] = []
        self._start_ns = 1000000000
        self._last_test_run = Mock()

    def as_attempt_to_fix(self, is_attempt: bool = True) -> "TestMockBuilder":
        """Set whether this is an attempt to fix."""
        self._is_attempt_to_fix = is_attempt
        return self

    def as_disabled(self, is_disabled: bool = True) -> "TestMockBuilder":
        """Set this test as disabled."""
        self._is_disabled = is_disabled
        return self

    def as_quarantined(self, is_quarantined: bool = True) -> "TestMockBuilder":
        """Set whether this test is quarantined."""
        self._is_quarantined = is_quarantined
        return self

    def with_test_runs(self, test_runs: t.List[Mock]) -> "TestMockBuilder":
        """Set test runs."""
        self._test_runs = test_runs
        return self

    def build(self) -> Mock:
        """Build the Test mock."""
        mock_test = Mock()
        mock_test.ref = self._test_ref
        mock_test.is_attempt_to_fix.return_value = self._is_attempt_to_fix
        mock_test.is_disabled.return_value = self._is_disabled
        mock_test.is_quarantined.return_value = self._is_quarantined
        mock_test.test_runs = self._test_runs
        mock_test.start_ns = self._start_ns
        mock_test.last_test_run = self._last_test_run
        return mock_test


class PytestItemMockBuilder:
    """Builder for creating pytest.Item mocks with flexible configuration."""

    def __init__(self, nodeid: str):
        self._nodeid = nodeid
        self._user_properties: t.List[t.Tuple[str, t.Any]] = []
        self._keywords: t.Dict[str, t.Any] = {}
        self._path = Mock()
        self._location = ("/fake/path.py", 10, "test_name")
        self._additional_attrs: t.Dict[str, t.Any] = {}

    def with_user_properties(self, properties: t.List[t.Tuple[str, t.Any]]) -> "PytestItemMockBuilder":
        """Set user properties."""
        self._user_properties = properties
        return self

    def with_keywords(self, keywords: t.Dict[str, t.Any]) -> "PytestItemMockBuilder":
        """Set keywords."""
        self._keywords = keywords
        return self

    def with_location(self, path: str, lineno: int, testname: str) -> "PytestItemMockBuilder":
        """Set test location info."""
        self._location = (path, lineno, testname)
        return self

    def with_attribute(self, name: str, value: t.Any) -> "PytestItemMockBuilder":
        """Add additional attribute."""
        self._additional_attrs[name] = value
        return self

    def build(self) -> Mock:
        """Build the pytest.Item mock."""
        mock_item = Mock()
        mock_item.nodeid = self._nodeid
        mock_item.add_marker = Mock()
        mock_item.reportinfo.return_value = self._location
        mock_item.path = self._path
        mock_item.path.absolute.return_value.parent = Path(self._location[0]).parent
        mock_item.user_properties = self._user_properties
        mock_item.keywords = self._keywords
        mock_item.location = self._location

        # Add any additional attributes
        for name, value in self._additional_attrs.items():
            setattr(mock_item, name, value)

        return mock_item


# =============================================================================
# UTILITY FUNCTIONS FOR TEST DATA CREATION
# =============================================================================


class TestDataFactory:
    """Factory for creating test data objects."""

    @staticmethod
    def create_test_ref(
        module_name: str = "test_module", suite_name: str = "test_suite.py", test_name: str = "test_function"
    ) -> TestRef:
        """Create a TestRef with sensible defaults."""
        module_ref = ModuleRef(module_name)
        suite_ref = SuiteRef(module_ref, suite_name)
        return TestRef(suite_ref, test_name)

    @staticmethod
    def create_suite_ref(module_name: str = "test_module", suite_name: str = "test_suite.py") -> SuiteRef:
        """Create a SuiteRef with sensible defaults."""
        module_ref = ModuleRef(module_name)
        return SuiteRef(module_ref, suite_name)

    @staticmethod
    def create_module_ref(module_name: str = "test_module") -> ModuleRef:
        """Create a ModuleRef with sensible defaults."""
        return ModuleRef(module_name)


# =============================================================================
# API CLIENT MOCK BUILDERS
# =============================================================================


class APIClientMockBuilder:
    """Builder for creating APIClient mocks with comprehensive network call prevention."""

    def __init__(self) -> None:
        self._skipping_enabled = False
        self._coverage_enabled = False
        self._auto_retries_enabled = False
        self._efd_enabled = False
        self._test_management_enabled = False
        self._known_tests_enabled = False
        self._skippable_items: t.Set[t.Union[TestRef, SuiteRef]] = set()
        self._known_tests: t.Set[TestRef] = set()

    def with_skipping_enabled(self, enabled: bool = True) -> "APIClientMockBuilder":
        """Enable/disable test skipping."""
        self._skipping_enabled = enabled
        return self

    def with_coverage_enabled(self, enabled: bool = True) -> "APIClientMockBuilder":
        """Enable/disable code coverage."""
        self._coverage_enabled = enabled
        return self

    def with_early_flake_detection(self, enabled: bool = True) -> "APIClientMockBuilder":
        """Enable/disable early flake detection."""
        self._efd_enabled = enabled
        return self

    def with_auto_retries(self, enabled: bool = True) -> "APIClientMockBuilder":
        """Enable/disable auto retries."""
        self._auto_retries_enabled = enabled
        return self

    def with_test_management(self, enabled: bool = True) -> "APIClientMockBuilder":
        """Enable/disable test management."""
        self._test_management_enabled = enabled
        return self

    def with_known_tests(
        self, enabled: bool = True, tests: t.Optional[t.Set[TestRef]] = None
    ) -> "APIClientMockBuilder":
        """Configure known tests."""
        self._known_tests_enabled = enabled
        if tests is not None:
            self._known_tests = tests
        return self

    def with_skippable_items(self, items: t.Set[t.Union[TestRef, SuiteRef]]) -> "APIClientMockBuilder":
        """Set skippable test items."""
        self._skippable_items = items
        return self

    def build(self) -> Mock:
        """Build the APIClient mock with comprehensive mocking."""
        mock_client = Mock()

        # Mock all API methods to prevent real HTTP calls
        mock_client.get_settings.return_value = Settings(
            early_flake_detection=EarlyFlakeDetectionSettings(
                enabled=self._efd_enabled,
            ),
            test_management=TestManagementSettings(enabled=self._test_management_enabled),
            auto_test_retries=AutoTestRetriesSettings(enabled=self._auto_retries_enabled),
            known_tests_enabled=self._known_tests_enabled,
            coverage_enabled=self._coverage_enabled,
            skipping_enabled=self._skipping_enabled,
            require_git=False,
            itr_enabled=self._skipping_enabled,
        )

        mock_client.get_known_tests.return_value = self._known_tests
        mock_client.get_test_management_properties.return_value = {}
        mock_client.get_known_commits.return_value = []
        mock_client.send_git_pack_file.return_value = None
        mock_client.get_skippable_tests.return_value = (
            self._skippable_items,
            "correlation-123" if self._skippable_items else None,
        )

        return mock_client


class BackendConnectorMockBuilder:
    """Builder for creating BackendConnector mocks that prevent real HTTP calls."""

    def __init__(self) -> None:
        self._post_json_responses: t.Dict[str, t.Any] = {}
        self._get_json_responses: t.Dict[str, t.Any] = {}
        self._request_responses: t.Dict[str, t.Any] = {}
        self._post_files_responses: t.Dict[str, t.Any] = {}

    def with_post_json_response(self, endpoint: str, response_data: t.Any) -> "BackendConnectorMockBuilder":
        """Mock a specific POST JSON endpoint response."""
        self._post_json_responses[endpoint] = response_data
        return self

    def with_get_json_response(self, endpoint: str, response_data: t.Any) -> "BackendConnectorMockBuilder":
        """Mock a specific POST JSON endpoint response."""
        self._get_json_responses[endpoint] = response_data
        return self

    def with_request_response(self, method: str, path: str, response_data: t.Any) -> "BackendConnectorMockBuilder":
        """Mock a specific HTTP request response."""
        self._request_responses[f"{method}:{path}"] = response_data
        return self

    def _make_404_response(self) -> BackendResult:
        return BackendResult(
            response=Mock(status=404), error_type=ErrorType.CODE_4XX, error_description="Not found", parsed_response={}
        )

    def build(self) -> Mock:
        """Build the BackendConnector mock."""
        mock_connector = Mock()

        # Mock methods to prevent real HTTP calls
        def mock_post_json(endpoint: str, data: t.Any, telemetry: t.Any = None) -> t.Tuple[Mock, t.Any]:
            if endpoint in self._post_json_responses:
                return BackendResult(response=Mock(status=200), parsed_response=self._post_json_responses[endpoint])
            return self._make_404_response()

        def mock_get_json(endpoint: str, max_attempts: int = 0) -> t.Tuple[Mock, t.Any]:
            if endpoint in self._get_json_responses:
                return BackendResult(response=Mock(status=200), parsed_response=self._get_json_responses[endpoint])
            return self._make_404_response()

        def mock_request(method: str, path: str, **kwargs: t.Any) -> t.Tuple[Mock, t.Any]:
            key = f"{method}:{path}"
            if key in self._request_responses:
                BackendResult(response=Mock(status=200), parsed_response=self._request_responses[key])
            return self._make_404_response()

        def mock_post_files(path: str, files: t.Any, **kwargs: t.Any) -> t.Tuple[Mock, t.Dict[str, t.Any]]:
            return BackendResult(response=Mock(status=200))

        mock_connector.post_json.side_effect = mock_post_json
        mock_connector.get_json.side_effect = mock_get_json
        mock_connector.request.side_effect = mock_request
        mock_connector.post_files.side_effect = mock_post_files

        return mock_connector


# =============================================================================
# CONVENIENCE FUNCTIONS
# =============================================================================


def pytest_item_mock(nodeid: str) -> PytestItemMockBuilder:
    """Create a PytestItemMockBuilder for the given nodeid."""
    return PytestItemMockBuilder(nodeid)


def session_manager_mock() -> "SessionManagerMockBuilder":
    """Create a SessionManagerMockBuilder with defaults."""
    return SessionManagerMockBuilder()


def mock_test_session(name: str = "test_session") -> TestSession:
    return TestSession(name)


def mock_test_module(module_ref: ModuleRef) -> TestModule:
    session = mock_test_session()
    module, _ = session.get_or_create_child(module_ref.name)
    return module


def mock_test_suite(suite_ref: SuiteRef) -> TestSuite:
    module = mock_test_module(suite_ref.module)
    suite, _ = module.get_or_create_child(suite_ref.name)
    return suite


def mock_test(test_ref: TestRef) -> Test:
    suite = mock_test_suite(test_ref.suite)
    test, _ = suite.get_or_create_child(test_ref.name)
    return test


def mock_test_run(test_ref: TestRef) -> TestRun:
    test = mock_test(test_ref)
    test_run = test.make_test_run()
    return test_run


def mock_api_client_settings(
    skipping_enabled: bool = False,
    coverage_enabled: bool = False,
    auto_retries_enabled: bool = False,
    efd_enabled: bool = False,
    test_management_enabled: bool = False,
    known_tests_enabled: bool = False,
    skippable_items: t.Optional[t.Set[t.Union[TestRef, SuiteRef]]] = None,
    known_tests: t.Optional[t.Set[TestRef]] = None,
) -> Mock:
    """Create a comprehensive API client mock - convenience function."""
    builder: "APIClientMockBuilder" = APIClientMockBuilder()

    if skipping_enabled:
        builder = builder.with_skipping_enabled()
    if coverage_enabled:
        builder = builder.with_coverage_enabled()
    if auto_retries_enabled:
        builder = builder.with_auto_retries()
    if efd_enabled:
        builder = builder.with_early_flake_detection()
    if test_management_enabled:
        builder = builder.with_test_management()
    if known_tests_enabled:
        builder = builder.with_known_tests(enabled=True, tests=known_tests)
    if skippable_items:
        builder = builder.with_skippable_items(skippable_items)

    return builder.build()


def mock_backend_connector() -> "BackendConnectorMockBuilder":
    """Create a BackendConnectorMockBuilder."""
    return BackendConnectorMockBuilder()


class BackendConnectorMockSetup:
    def get_connector_for_subdomain(self, subdomain: str) -> Mock:
        return mock_backend_connector().build()


@contextlib.contextmanager
def setup_standard_mocks() -> t.Generator[None, None, None]:
    """Mock calls used by the session manager to get git and platform tags."""
    with (
        patch.multiple(
            "ddtrace.testing.internal.session_manager",
            get_env_tags=Mock(return_value={}),
            get_platform_tags=Mock(return_value={}),
            Git=Mock(return_value=get_mock_git_instance()),
        ),
        patch.object(BackendConnectorSetup, "detect_setup", return_value=BackendConnectorMockSetup()),
    ):
        yield


def network_mocks() -> t.ContextManager[t.Any]:
    """Create comprehensive mocks that prevent ALL network calls at multiple levels."""
    from contextlib import ExitStack

    def _create_stack() -> t.ContextManager[t.Any]:
        stack = ExitStack()

        # Mock the session manager dependencies
        stack.enter_context(
            patch.multiple(
                "ddtrace.testing.internal.session_manager",
                get_env_tags=Mock(return_value={}),
                get_platform_tags=Mock(return_value={}),
                Git=Mock(return_value=get_mock_git_instance()),
            )
        )

        stack.enter_context(
            patch.object(BackendConnectorSetup, "detect_setup", return_value=BackendConnectorMockSetup())
        )

        # Mock the HTTP connector to prevent any real HTTP calls
        mock_connector = mock_backend_connector().build()
        stack.enter_context(patch("ddtrace.testing.internal.http.BackendConnector", return_value=mock_connector))

        # Mock the API client constructor to ensure our mock is used
        stack.enter_context(patch("ddtrace.testing.internal.session_manager.APIClient"))

        # Mock the writer to prevent any HTTP calls from the writer
        mock_writer = Mock()
        mock_writer.flush.return_value = None
        mock_writer._send_events.return_value = None
        stack.enter_context(patch("ddtrace.testing.internal.writer.TestOptWriter", return_value=mock_writer))
        stack.enter_context(patch("ddtrace.testing.internal.writer.TestCoverageWriter", return_value=mock_writer))

        return stack

    return _create_stack()


class EventCapture:
    """
    Utilities for capturing events generated during a test run.
    """

    @classmethod
    @contextlib.contextmanager
    def capture(cls) -> t.Generator[EventCapture, None, None]:
        """
        Mock the event writer to capture events sent during a test run.

        Returns a context manager that can be queried after the test run with the methods below.

        Example usage:
            with EventCapture.capture() as event_capture:
                pytester.inline_run(...)

            all_events = list(event_capture.events())

            [session_event] = event_capture.events_by_type("test_session_end")

            test_event = event_capture.event_by_test_name("test_foo")
        """
        with patch.object(TestOptWriter, "put_event") as put_event_mock:
            yield cls(put_event_mock)

    def __init__(self, put_event_mock: Mock) -> None:
        self.put_event_mock = put_event_mock

    def events(self) -> t.Iterable[Event]:
        for args, kwargs in self.put_event_mock.call_args_list:
            event = args[0]
            yield event

    def events_by_type(self, event_type: str) -> t.Iterable[Event]:
        for event in self.events():
            if event["type"] == event_type:
                yield event

    def events_by_test_name(self, test_name: str) -> t.Iterable[Event]:
        for event in self.events():
            if event["type"] == "test" and event["content"]["meta"]["test.name"] == test_name:
                yield event

    def event_by_test_name(self, test_name: str) -> Event:
        try:
            return next(self.events_by_test_name(test_name))
        except StopIteration:
            raise AssertionError(f"Expected event with test name {test_name!r}, found none")


def test_report(
    nodeid: str = "foo.py::test_foo",
    location: t.Tuple[str, int, str] = ("foo.py", 42, "foo"),
    outcome: str = "passed",
    longrepr: t.Any = None,
    when: str = "call",
    keywords: t.Optional[t.Dict[str, str]] = None,
    wasxfail: t.Any = None,
):
    report = TestReport(
        nodeid=nodeid, location=location, outcome=outcome, longrepr=longrepr, when=when, keywords=keywords or {}
    )
    if wasxfail:
        setattr(report, "wasxfail", wasxfail)
    return report
