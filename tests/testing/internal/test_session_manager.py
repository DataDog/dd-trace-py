import os

import pytest

from ddtestpy.internal.ci import CITag
from ddtestpy.internal.test_data import ModuleRef
from ddtestpy.internal.test_data import SuiteRef
from ddtestpy.internal.test_data import TestRef
from tests.mocks import MockDefaults
from tests.mocks import session_manager_mock


class TestSessionManagerIsSkippableTest:
    """Test the new is_skippable_test method in SessionManager."""

    def setup_method(self) -> None:
        """Set up test environment and mocks."""
        self.test_env = MockDefaults.test_environment()

    def test_skipping_disabled_returns_false(self) -> None:
        """Test that is_skippable_test returns False when skipping is disabled."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create session manager with skipping disabled
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(False)
            .with_skippable_items({test_ref})  # Even if test is in skippable_items
            .build_real_with_mocks(self.test_env)
        )

        # Should return False because skipping is disabled
        assert session_manager.is_skippable_test(test_ref) is False

    def test_test_in_skippable_items_returns_true(self) -> None:
        """Test that is_skippable_test returns True when test is in skippable_items."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create session manager with test in skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({test_ref})
            .build_real_with_mocks(self.test_env)
        )

        # Should return True because test is in skippable_items
        assert session_manager.is_skippable_test(test_ref) is True

    def test_suite_in_skippable_items_returns_true(self) -> None:
        """Test that is_skippable_test returns True when test's suite is in skippable_items."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create session manager with suite in skippable_items (but not the individual test)
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({suite_ref})
            .build_real_with_mocks(self.test_env)
        )

        # Should return True because test's suite is in skippable_items
        assert session_manager.is_skippable_test(test_ref) is True

    def test_both_test_and_suite_in_skippable_items_returns_true(self) -> None:
        """Test that is_skippable_test returns True when both test and suite are in skippable_items."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create session manager with both test and suite in skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({test_ref, suite_ref})
            .build_real_with_mocks(self.test_env)
        )

        # Should return True
        assert session_manager.is_skippable_test(test_ref) is True

    def test_neither_test_nor_suite_in_skippable_items_returns_false(self) -> None:
        """Test that is_skippable_test returns False when neither test nor suite is in skippable_items."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create different test/suite that are not the ones we're testing
        other_module_ref = ModuleRef("other_module")
        other_suite_ref = SuiteRef(other_module_ref, "other_suite.py")
        other_test_ref = TestRef(other_suite_ref, "other_function")

        # Create session manager with different test/suite in skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({other_test_ref, other_suite_ref})
            .build_real_with_mocks(self.test_env)
        )

        # Should return False because neither our test nor suite is in skippable_items
        assert session_manager.is_skippable_test(test_ref) is False

    def test_empty_skippable_items_returns_false(self) -> None:
        """Test that is_skippable_test returns False when skippable_items is empty."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref = TestRef(suite_ref, "test_function")

        # Create session manager with empty skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items(set())
            .build_real_with_mocks(self.test_env)
        )

        # Should return False because skippable_items is empty
        assert session_manager.is_skippable_test(test_ref) is False

    def test_different_test_same_suite_name_different_module(self) -> None:
        """Test that suite matching is exact (including module)."""
        # Create test references
        module_ref1 = ModuleRef("module1")
        module_ref2 = ModuleRef("module2")
        suite_ref1 = SuiteRef(module_ref1, "test_suite.py")
        suite_ref2 = SuiteRef(module_ref2, "test_suite.py")  # Same suite name, different module
        test_ref = TestRef(suite_ref1, "test_function")

        # Create session manager with suite from different module in skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({suite_ref2})  # Different module, same suite name
            .build_real_with_mocks(self.test_env)
        )

        # Should return False because the suite is from a different module
        assert session_manager.is_skippable_test(test_ref) is False

    def test_multiple_tests_same_skippable_suite(self) -> None:
        """Test that multiple tests from the same skippable suite are all skippable."""
        # Create test references
        module_ref = ModuleRef("test_module")
        suite_ref = SuiteRef(module_ref, "test_suite.py")
        test_ref1 = TestRef(suite_ref, "test_function1")
        test_ref2 = TestRef(suite_ref, "test_function2")
        test_ref3 = TestRef(suite_ref, "test_function3")

        # Create session manager with suite in skippable_items
        session_manager = (
            session_manager_mock()
            .with_skipping_enabled(True)
            .with_skippable_items({suite_ref})
            .build_real_with_mocks(self.test_env)
        )

        # All tests from the same suite should be skippable
        assert session_manager.is_skippable_test(test_ref1) is True
        assert session_manager.is_skippable_test(test_ref2) is True
        assert session_manager.is_skippable_test(test_ref3) is True


class TestSessionNameTest:
    def test_session_name_explicitly_from_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {"DD_TEST_SESSION_NAME": "the_name", "DD_API_KEY": "somekey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
        monkeypatch.setattr(os, "environ", env)
        session_manager = session_manager_mock().with_env_tags({CITag.JOB_NAME: "the_job"}).build_real_with_mocks(env)

        expected_name = "the_name"
        assert session_manager._get_test_session_name() == expected_name
        assert session_manager.writer.metadata["*"]["test_session.name"] == expected_name

    def test_session_name_from_job_name(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {"DD_API_KEY": "somekey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
        monkeypatch.setattr(os, "environ", env)
        session_manager = session_manager_mock().with_env_tags({CITag.JOB_NAME: "the_job"}).build_real_with_mocks(env)

        expected_name = "the_job-pytest"
        assert session_manager._get_test_session_name() == expected_name
        assert session_manager.writer.metadata["*"]["test_session.name"] == expected_name

    def test_session_name_from_test_command(self, monkeypatch: pytest.MonkeyPatch) -> None:
        env = {"DD_API_KEY": "somekey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
        monkeypatch.setattr(os, "environ", env)
        session_manager = session_manager_mock().with_env_tags({}).build_real_with_mocks(env)

        expected_name = "pytest"
        assert session_manager._get_test_session_name() == expected_name
        assert session_manager.writer.metadata["*"]["test_session.name"] == expected_name
