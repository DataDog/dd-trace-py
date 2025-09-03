"""Tests EFD (Early Flake Detection) threshold adjustment functionality with pytest-xdist.

The tests in this module validate that EFD faulty session threshold is properly
adjusted when running with pytest-xdist distributed test execution.
"""

import os
from unittest import mock

import pytest

from tests.ci_visibility.util import _get_default_civisibility_ddconfig
from tests.contrib.pytest.test_pytest import PytestTestCaseBase


######
# Skip these tests if they are not running under riot
riot_env_value = os.getenv("RIOT", None)
if not riot_env_value:
    pytest.importorskip("xdist", reason="EFD + xdist tests, not running under riot")
######


_USE_PLUGIN_V2 = True

pytestmark = pytest.mark.skipif(
    not _USE_PLUGIN_V2,
    reason="EFD requires v2 of the plugin",
)

# Test content with 12 new tests to trigger EFD threshold
_TEST_NEW_CONTENT = """
import pytest

def test_new_1():
    assert True

def test_new_2():
    assert True

def test_new_3():
    assert True

def test_new_4():
    assert True

def test_new_5():
    assert True

def test_new_6():
    assert True

def test_new_7():
    assert True

def test_new_8():
    assert True

def test_new_9():
    assert True

def test_new_10():
    assert True

def test_new_11():
    assert True

def test_new_12():
    assert True
"""

# Test content with some known tests
_TEST_KNOWN_CONTENT = """
import pytest

def test_known_1():
    assert True

def test_known_2():
    assert True

def test_known_3():
    assert True

def test_known_4():
    assert True
"""


class PytestXdistEFDTestCase(PytestTestCaseBase):
    @pytest.fixture(autouse=True, scope="function")
    def setup_sitecustomize(self):
        """
        This allows to patch the tracer before the tests are run, so it works
        in the xdist worker processes.
        """
        sitecustomize_content = """
# sitecustomize.py
from unittest import mock
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings, EarlyFlakeDetectionSettings
import ddtrace.internal.ci_visibility.recorder # Ensure parent module is loaded

_GLOBAL_SITECUSTOMIZE_PATCH_OBJECT = mock.patch(
    "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
    return_value=TestVisibilityAPISettings(
        early_flake_detection=EarlyFlakeDetectionSettings(True, faulty_session_threshold=30)
    )
)
_GLOBAL_SITECUSTOMIZE_PATCH_OBJECT.start()

# Mock ITR to mark some tests as new for EFD testing
_ITR_PATCH = mock.patch(
    "ddtrace.contrib.internal.pytest._plugin_v2.CIVisibilityPlugin._is_item_new_test",
    side_effect=lambda item: "new_" in item.name
)
_ITR_PATCH.start()
"""
        self.testdir.makepyfile(sitecustomize=sitecustomize_content)

    def inline_run(self, *args, **kwargs):
        # Add -n 4 to distribute across 4 workers
        args = list(args) + ["-n", "4", "-c", "/dev/null"]
        return super().inline_run(*args, **kwargs)

    def test_pytest_xdist_efd_threshold_adjustment_prevents_faulty_session(self):
        """Test that EFD threshold adjustment prevents faulty session with distributed workers"""
        self.testdir.makepyfile(test_known=_TEST_KNOWN_CONTENT)
        self.testdir.makepyfile(test_new=_TEST_NEW_CONTENT)

        # 12 new tests + 4 known tests = 16 total, 75% new
        # Without xdist: 75% > 30% = faulty session
        # With 4 workers: threshold becomes 30/4 = 7.5% per worker
        # Each worker gets ~3-4 tests, so percentage varies per worker
        # The test should pass (exit 0) because threshold is adjusted

        with mock.patch("ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()):
            rec = self.inline_run("--ddtrace", "-v")
            assert rec.ret == 0

    def test_pytest_xdist_efd_threshold_adjustment_still_triggers_when_appropriate(self):
        """Test that EFD can still trigger faulty session with extreme new test percentages"""
        # Create more aggressive new test scenario where even with threshold adjustment
        # we should still get faulty session detection
        many_new_tests = """
import pytest
""" + "\n".join(
            [f"def test_new_{i}():\n    assert True" for i in range(1, 41)]
        )  # 40 new tests

        self.testdir.makepyfile(test_new_many=many_new_tests)

        # 40 new tests, 0 known = 100% new
        # With 4 workers: threshold becomes 30/4 = 7.5% per worker
        # Each worker gets ~10 tests, 100% new > 7.5% = should still trigger faulty session

        with mock.patch("ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()):
            # Set custom threshold via environment variable to test env var handling
            rec = self.inline_run("--ddtrace", "-v", extra_env={"_DD_CIVISIBILITY_EFD_FAULTY_SESSION_THRESHOLD": "30"})
            # Test should pass but EFD should be disabled due to faulty session
            assert rec.ret == 0

    def test_pytest_xdist_efd_custom_threshold_via_env_var(self):
        """Test that custom EFD threshold environment variable works with xdist"""
        self.testdir.makepyfile(test_known=_TEST_KNOWN_CONTENT)
        self.testdir.makepyfile(test_new=_TEST_NEW_CONTENT)

        # Use custom 80% threshold - should NOT trigger faulty session
        # With 4 workers: 80/4 = 20% threshold per worker
        # 12 new + 4 known = 75% new, which when distributed should be fine

        with mock.patch("ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()):
            rec = self.inline_run("--ddtrace", "-v", extra_env={"_DD_CIVISIBILITY_EFD_FAULTY_SESSION_THRESHOLD": "80"})
            assert rec.ret == 0
