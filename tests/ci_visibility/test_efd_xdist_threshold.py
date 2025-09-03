"""Tests for EFD faulty session threshold adjustment in pytest-xdist environments."""

from pathlib import Path

import pytest

from ddtrace.ext.test_visibility._test_visibility_base import TestId
from ddtrace.ext.test_visibility._test_visibility_base import TestModuleId
from ddtrace.ext.test_visibility._test_visibility_base import TestSuiteId
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility.api._base import TestVisibilitySessionSettings
from ddtrace.internal.ci_visibility.api._session import TestVisibilitySession
from ddtrace.internal.ci_visibility.api._module import TestVisibilityModule
from ddtrace.internal.ci_visibility.api._suite import TestVisibilitySuite
from ddtrace.internal.ci_visibility.api._test import TestVisibilityTest
from ddtrace.internal.ci_visibility.telemetry.constants import TEST_FRAMEWORKS
from tests.utils import DummyTracer


class TestEFDXdistThreshold:
    """Test EFD faulty session threshold adjustment for pytest-xdist."""
    
    def _get_session_settings(self, efd_settings=None):
        """Helper method to create session settings with EFD settings."""
        return TestVisibilitySessionSettings(
            tracer=DummyTracer(),
            test_service="efd_test_service",
            test_command="efd_test_command",
            test_framework="efd_test_framework",
            test_framework_metric_name=TEST_FRAMEWORKS.MANUAL,
            test_framework_version="0.0",
            session_operation_name="efd_session",
            module_operation_name="efd_module",
            suite_operation_name="efd_suite",
            test_operation_name="efd_test",
            workspace_path=Path().absolute(),
            efd_settings=efd_settings or EarlyFlakeDetectionSettings(),
        )

    def test_efd_session_faulty_xdist_threshold_adjustment_with_execnet(self, monkeypatch):
        """Test that EFD threshold is adjusted for xdist workers using execnet-passed worker count"""
        # Set environment variable to simulate xdist worker
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw2")  # Worker 2 of 4
        
        # Simulate pytest global variable set by main process via execnet
        pytest.xdist_total_workers = 4
        
        efd_settings = EarlyFlakeDetectionSettings(True, faulty_session_threshold=30)
        ssettings = self._get_session_settings(efd_settings=efd_settings)
        test_session = TestVisibilitySession(session_settings=ssettings)

        # Add tests: 10 known, 10 new (50% new)
        # With 4 workers, threshold should be 30/4 = 7.5%
        # So 50% new tests should trigger faulty session
        m1_id = TestModuleId("module_1")
        m1 = TestVisibilityModule(m1_id.name, session_settings=ssettings)
        test_session.add_child(m1_id, m1)
        m1_s1_id = TestSuiteId(m1_id, "m1_s1")
        m1_s1 = TestVisibilitySuite(m1_s1_id.name, session_settings=ssettings)
        m1.add_child(m1_s1_id, m1_s1)

        # Known tests
        for i in range(10):
            test_name = f"known_t{i}"
            m1_s1.add_child(
                TestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=False),
            )

        # New tests
        for i in range(10):
            test_name = f"new_t{i}"
            m1_s1.add_child(
                TestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=True),
            )

        # 50% new tests > 7.5% threshold = faulty session
        assert test_session.efd_is_faulty_session() is True
        
        # Clean up
        if hasattr(pytest, "xdist_total_workers"):
            delattr(pytest, "xdist_total_workers")

    def test_efd_session_not_faulty_xdist_threshold_adjustment(self, monkeypatch):
        """Test that EFD does not trigger faulty session with adjusted xdist threshold"""
        # Set environment variable to simulate xdist worker  
        monkeypatch.setenv("PYTEST_XDIST_WORKER", "gw0")
        
        # Simulate pytest global variable set by main process via execnet
        pytest.xdist_total_workers = 4
        
        efd_settings = EarlyFlakeDetectionSettings(True, faulty_session_threshold=30)
        ssettings = self._get_session_settings(efd_settings=efd_settings)
        test_session = TestVisibilitySession(session_settings=ssettings)

        # Add tests: 50 known, 2 new (3.8% new)
        # With 4 workers, threshold should be 30/4 = 7.5%
        # So 3.8% new tests should NOT trigger faulty session
        m1_id = TestModuleId("module_1")
        m1 = TestVisibilityModule(m1_id.name, session_settings=ssettings)
        test_session.add_child(m1_id, m1)
        m1_s1_id = TestSuiteId(m1_id, "m1_s1")
        m1_s1 = TestVisibilitySuite(m1_s1_id.name, session_settings=ssettings)
        m1.add_child(m1_s1_id, m1_s1)

        # Known tests
        for i in range(50):
            test_name = f"known_t{i}"
            m1_s1.add_child(
                TestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=False),
            )

        # New tests  
        for i in range(2):
            test_name = f"new_t{i}"
            m1_s1.add_child(
                TestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=True),
            )

        # 3.8% new tests < 7.5% threshold = not faulty session
        assert test_session.efd_is_faulty_session() is False
        
        # Clean up
        if hasattr(pytest, "xdist_total_workers"):
            delattr(pytest, "xdist_total_workers")

    def test_efd_session_faulty_custom_threshold_env_var(self, monkeypatch):
        """Test that EFD uses custom threshold from _DD_CIVISIBILITY_EFD_FAULTY_SESSION_THRESHOLD"""
        # Set custom threshold to 20% via the existing environment variable
        monkeypatch.setenv("_DD_CIVISIBILITY_EFD_FAULTY_SESSION_THRESHOLD", "20")
        
        # Create settings with custom threshold (will read from env var via _get_faulty_session_threshold)
        from ddtrace.internal.ci_visibility._api_client import _get_faulty_session_threshold
        custom_threshold = _get_faulty_session_threshold()
        
        efd_settings = EarlyFlakeDetectionSettings(True, faulty_session_threshold=custom_threshold)
        ssettings = self._get_session_settings(efd_settings=efd_settings)
        test_session = TestVisibilitySession(session_settings=ssettings)

        # Add tests: 80 known, 20 new (20% new)
        # Custom threshold is 20%, so 20% new tests should NOT trigger faulty session
        m1_id = TestModuleId("module_1")
        m1 = TestVisibilityModule(m1_id.name, session_settings=ssettings)
        test_session.add_child(m1_id, m1)
        m1_s1_id = TestSuiteId(m1_id, "m1_s1")
        m1_s1 = TestVisibilitySuite(m1_s1_id.name, session_settings=ssettings)
        m1.add_child(m1_s1_id, m1_s1)

        # Known tests
        for i in range(80):
            test_name = f"known_t{i}"
            m1_s1.add_child(
                TestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=False),
            )

        # New tests
        for i in range(20):
            test_name = f"new_t{i}"
            m1_s1.add_child(
                TestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=True),
            )

        # 20% new tests = 20% threshold = not faulty session (not greater than)
        assert test_session.efd_is_faulty_session() is False

        # Now test with 21 new tests (20.8% new) which should trigger faulty session
        test_name = f"new_t_extra"
        m1_s1.add_child(
            TestId(m1_s1_id, name=test_name),
            TestVisibilityTest(test_name, session_settings=ssettings, is_new=True),
        )
        
        # Reset cached result to force recalculation
        test_session._efd_is_faulty_session = None
        
        # 20.8% new tests > 20% threshold = faulty session
        assert test_session.efd_is_faulty_session() is True

    def test_efd_session_no_xdist_adjustment(self, monkeypatch):
        """Test that EFD does not adjust threshold when not running in xdist"""
        # Ensure no xdist environment variables are set and no pytest globals
        monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
        if hasattr(pytest, "xdist_total_workers"):
            delattr(pytest, "xdist_total_workers")
        
        efd_settings = EarlyFlakeDetectionSettings(True, faulty_session_threshold=30)
        ssettings = self._get_session_settings(efd_settings=efd_settings)
        test_session = TestVisibilitySession(session_settings=ssettings)

        # Add tests: 70 known, 30 new (30% new)
        # Without xdist, threshold should remain 30%
        # So 30% new tests should NOT trigger faulty session (not greater than)
        m1_id = TestModuleId("module_1")
        m1 = TestVisibilityModule(m1_id.name, session_settings=ssettings)
        test_session.add_child(m1_id, m1)
        m1_s1_id = TestSuiteId(m1_id, "m1_s1")
        m1_s1 = TestVisibilitySuite(m1_s1_id.name, session_settings=ssettings)
        m1.add_child(m1_s1_id, m1_s1)

        # Known tests
        for i in range(70):
            test_name = f"known_t{i}"
            m1_s1.add_child(
                TestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=False),
            )

        # New tests
        for i in range(30):
            test_name = f"new_t{i}"
            m1_s1.add_child(
                TestId(m1_s1_id, name=test_name),
                TestVisibilityTest(test_name, session_settings=ssettings, is_new=True),
            )

        # 30% new tests = 30% threshold = not faulty session
        assert test_session.efd_is_faulty_session() is False

        # Add one more new test to trigger faulty session
        test_name = f"new_t_extra"
        m1_s1.add_child(
            TestId(m1_s1_id, name=test_name),
            TestVisibilityTest(test_name, session_settings=ssettings, is_new=True),
        )
        
        # Reset cached result
        test_session._efd_is_faulty_session = None
        
        # 30.7% new tests > 30% threshold = faulty session
        assert test_session.efd_is_faulty_session() is True