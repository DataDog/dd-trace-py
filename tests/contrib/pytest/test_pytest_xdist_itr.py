"""Tests Intelligent Test Runner (ITR) functionality interacting with pytest-xdist.

The tests in this module validate the interaction between ITR and pytest-xdist.
"""
import os  # Just for the RIOT env var check
from unittest import mock

import pytest

from ddtrace.contrib.internal.pytest._utils import _USE_PLUGIN_V2
from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_itr
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings

from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.contrib.pytest.test_pytest import PytestTestCaseBase


######
# Skip these tests if they are not running under riot
riot_env_value = os.getenv("RIOT", None)
if not riot_env_value:
    pytest.importorskip("xdist", reason="ITR + xdist tests, not running under riot")
######


pytestmark = pytest.mark.skipif(
    not (_USE_PLUGIN_V2 and _pytest_version_supports_itr()),
    reason="ITR requires v2 of the plugin and pytest >=7.0",
)

_TEST_PASS_CONTENT = """
import unittest

def test_func_pass():
    assert True

class SomeTestCase(unittest.TestCase):
    def test_class_func_pass(self):
        assert True
"""

_TEST_FAIL_CONTENT = """
import pytest
import unittest

def test_func_fail():
    assert False

class SomeTestCase(unittest.TestCase):
    def test_class_func_fail(self):
        assert False
"""

_TEST_SKIP_CONTENT = """
import pytest
import unittest

@pytest.mark.skip
def test_func_skip_mark():
    assert True

def test_func_skip_inside():
    pytest.skip()

class SomeTestCase(unittest.TestCase):
    @pytest.mark.skip
    def test_class_func_skip_mark(self):
        assert True

    def test_class_func_skip_inside(self):
        pytest.skip()
"""


class PytestXdistITRTestCase(PytestTestCaseBase):
    def inline_run(self, *args, **kwargs):
        # Add -n 2 to the end of the command line arguments
        args = list(args) + ["-n", "2"]
        return super().inline_run(*args, **kwargs)

    def test_pytest_xdist_itr_skips_tests(self):
        """Test that ITR skips tests when enabled."""
        # Create a simplified sitecustomize with just the essential ITR setup
        itr_skipping_sitecustomize = """
# sitecustomize.py - Simplified ITR setup for xdist
from unittest import mock

# Import required modules
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestManagementSettings
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.ext.test_visibility._item_ids import TestSuiteId, TestModuleId


# Create ITR settings and data
itr_settings = TestVisibilityAPISettings(
    coverage_enabled=False, skipping_enabled=True, require_git=False, itr_enabled=True,
    flaky_test_retries_enabled=False, known_tests_enabled=False,
    early_flake_detection=EarlyFlakeDetectionSettings(), test_management=TestManagementSettings()
)

# Create skippable test suites
skippable_suites = {
    TestSuiteId(TestModuleId(""), "test_pass.py"),
    TestSuiteId(TestModuleId(""), "test_fail.py")
}
itr_data = ITRData(correlation_id="12345678-1234-1234-1234-123456789012", skippable_items=skippable_suites)

# Mock API calls to return our settings
mock.patch(
    "ddtrace.internal.ci_visibility._api_client.AgentlessTestVisibilityAPIClient.fetch_settings",
    return_value=itr_settings
).start()

# Set ITR data when CIVisibility is enabled
import ddtrace.internal.ci_visibility.recorder
CIVisibility = ddtrace.internal.ci_visibility.recorder.CIVisibility
original_enable = CIVisibility.enable

def patched_enable(cls, *args, **kwargs):
    result = original_enable(*args, **kwargs)
    if cls._instance:
        cls._instance._itr_data = itr_data
    return result

CIVisibility.enable = classmethod(patched_enable)
"""
        self.testdir.makepyfile(sitecustomize=itr_skipping_sitecustomize)
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.chdir()

        itr_settings = TestVisibilityAPISettings(
            coverage_enabled=False,
            skipping_enabled=True,
            require_git=False,
            itr_enabled=True,
            flaky_test_retries_enabled=False,
            known_tests_enabled=False,
            early_flake_detection=EarlyFlakeDetectionSettings(),
            test_management=TestManagementSettings(),
        )

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features", return_value=itr_settings
        ):
            rec = self.inline_run("--ddtrace", extra_env={
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                "DD_API_KEY": "foobar.baz",
                "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "0",
            })
            assert rec.ret == 0  # All tests skipped, so exit code is 0

            # Verify ITR worked
            spans = self.pop_spans()
            session_span = [span for span in spans if span.get_tag("type") == "test_session_end"][0]
            assert session_span.get_tag("test.itr.tests_skipping.enabled") == "true"
            assert session_span.get_metric("test.itr.tests_skipping.count") == 4  # 4 tests skipped
