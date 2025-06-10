"""Tests Auto Test Retries (ATR) functionality interacting with pytest-xdist.

The tests in this module only validate the exit status from pytest-xdist.
"""
import os  # Just for the RIOT env var check
from unittest import mock

import pytest

from ddtrace.contrib.internal.pytest._utils import _USE_PLUGIN_V2
from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_atr
from tests.ci_visibility.util import _get_default_civisibility_ddconfig
from tests.contrib.pytest.test_pytest import PytestTestCaseBase


######
# Skip these tests if they are not running under riot
riot_env_value = os.getenv("RIOT", None)
if not riot_env_value:
    pytest.importorskip("xdist", reason="Auto Test Retries + xdist tests, not running under riot")
######


pytestmark = pytest.mark.skipif(
    not (_USE_PLUGIN_V2 and _pytest_version_supports_atr()),
    reason="Auto Test Retries requires v2 of the plugin and pytest >=7.0",
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

_test_func_retries_skip_count = 0

def test_func_retries_skip():
    global _test_func_retries_skip_count
    _test_func_retries_skip_count += 1
    if _test_func_retries_skip_count > 1:
        pytest.skip()
    assert False

_test_class_func_retries_skip_count = 0

class SomeTestCase(unittest.TestCase):
    def test_class_func_fail(self):
        assert False

    def test_class_func_retries_skip(self):
        global _test_class_func_retries_skip_count
        _test_class_func_retries_skip_count += 1
        if _test_class_func_retries_skip_count > 1:
            pytest.skip()
        assert False
"""


_TEST_PASS_ON_RETRIES_CONTENT = """
import unittest

_test_func_passes_4th_retry_count = 0
def test_func_passes_4th_retry():
    global _test_func_passes_4th_retry_count
    _test_func_passes_4th_retry_count += 1
    assert _test_func_passes_4th_retry_count == 5

_test_func_passes_1st_retry_count = 0
def test_func_passes_1st_retry():
    global _test_func_passes_1st_retry_count
    _test_func_passes_1st_retry_count += 1
    assert _test_func_passes_1st_retry_count == 2

class SomeTestCase(unittest.TestCase):
    _test_func_passes_4th_retry_count = 0

    def test_func_passes_4th_retry(self):
        SomeTestCase._test_func_passes_4th_retry_count += 1
        assert SomeTestCase._test_func_passes_4th_retry_count == 5

"""

_TEST_ERRORS_CONTENT = """
import pytest
import unittest

@pytest.fixture
def fixture_fails_setup():
    assert False

def test_func_fails_setup(fixture_fails_setup):
    assert True

@pytest.fixture
def fixture_fails_teardown():
    yield
    assert False

def test_func_fails_teardown(fixture_fails_teardown):
    assert True
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


class PytestXdistATRTestCase(PytestTestCaseBase):
    @pytest.fixture(autouse=True, scope="function")
    def setup_sitecustomize(self):
        """
        This allows to patch the tracer before the tests are run, so it works
        in the xdist worker processes.
        """
        sitecustomize_content = """
# sitecustomize.py - Cross-process ATR mocking for xdist
import os
from unittest import mock
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings, EarlyFlakeDetectionSettings, TestManagementSettings

# Ensure environment variables are set for agentless mode
os.environ["DD_CIVISIBILITY_AGENTLESS_ENABLED"] = "1"
os.environ["DD_API_KEY"] = "foobar.baz"
os.environ["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "0"

# Mock ddconfig to enable agentless mode
from ddtrace import config as ddconfig
ddconfig._ci_visibility_agentless_enabled = True

# Ensure parent module is loaded
import ddtrace.internal.ci_visibility.recorder

# Create ATR-enabled settings for worker processes
atr_enabled_settings = TestVisibilityAPISettings(
    coverage_enabled=False,
    skipping_enabled=False,
    require_git=False,
    itr_enabled=False,
    flaky_test_retries_enabled=True,
    known_tests_enabled=False,
    early_flake_detection=EarlyFlakeDetectionSettings(),
    test_management=TestManagementSettings(),
)

# Apply the settings mock globally for all processes
_GLOBAL_SITECUSTOMIZE_PATCH_OBJECT = mock.patch(
    "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
    return_value=atr_enabled_settings
)
_GLOBAL_SITECUSTOMIZE_PATCH_OBJECT.start()
"""
        self.testdir.makepyfile(sitecustomize=sitecustomize_content)

    def inline_run(self, *args, **kwargs):
        # Add -n 2 to the end of the command line arguments
        args = list(args) + ["-n", "2", "-c", "/dev/null"]
        return super().inline_run(*args, **kwargs)

    def test_pytest_xdist_atr_no_ddtrace_does_not_retry(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)
        rec = self.inline_run()
        assert rec.ret == 1

    def test_pytest_xdist_atr_env_var_disables_retrying(self):
        # Create a test-specific sitecustomize with ATR disabled settings
        atr_disabled_sitecustomize = """
# sitecustomize.py - Cross-process ATR disabled mocking for xdist
import os
from unittest import mock
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings, EarlyFlakeDetectionSettings, TestManagementSettings

# Ensure environment variables are set for agentless mode
os.environ["DD_CIVISIBILITY_AGENTLESS_ENABLED"] = "1"
os.environ["DD_API_KEY"] = "foobar.baz"
os.environ["DD_INSTRUMENTATION_TELEMETRY_ENABLED"] = "0"
os.environ["DD_CIVISIBILITY_FLAKY_RETRY_ENABLED"] = "0"  # Disable ATR via env var

# Mock ddconfig to enable agentless mode
from ddtrace import config as ddconfig
ddconfig._ci_visibility_agentless_enabled = True

# Ensure parent module is loaded
import ddtrace.internal.ci_visibility.recorder

# Create ATR-disabled settings for worker processes (environment variable takes precedence)
atr_disabled_settings = TestVisibilityAPISettings(
    coverage_enabled=False,
    skipping_enabled=False,
    require_git=False,
    itr_enabled=False,
    flaky_test_retries_enabled=False,
    known_tests_enabled=False,
    early_flake_detection=EarlyFlakeDetectionSettings(),
    test_management=TestManagementSettings(),
)

# Apply the settings mock globally for all processes
_GLOBAL_SITECUSTOMIZE_PATCH_OBJECT = mock.patch(
    "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
    return_value=atr_disabled_settings
)
_GLOBAL_SITECUSTOMIZE_PATCH_OBJECT.start()
"""
        self.testdir.makepyfile(sitecustomize=atr_disabled_sitecustomize)

        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        # Create ATR-disabled settings for the main process
        from ddtrace.internal.ci_visibility._api_client import (
            TestVisibilityAPISettings,
            EarlyFlakeDetectionSettings,
            TestManagementSettings,
        )

        atr_disabled_settings = TestVisibilityAPISettings(
            coverage_enabled=False,
            skipping_enabled=False,
            require_git=False,
            itr_enabled=False,
            flaky_test_retries_enabled=False,
            known_tests_enabled=False,
            early_flake_detection=EarlyFlakeDetectionSettings(),
            test_management=TestManagementSettings(),
        )

        # Use the proper CI Visibility test environment setup
        mock_ddconfig = _get_default_civisibility_ddconfig()
        mock_ddconfig._ci_visibility_agentless_enabled = True

        with mock.patch("ddtrace.internal.ci_visibility.recorder.ddconfig", mock_ddconfig), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=atr_disabled_settings,
        ):
            extra_env = {
                "DD_API_KEY": "foobar.baz",
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "0",
                "DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "0",
            }
            rec = self.inline_run("--ddtrace", "-s", extra_env=extra_env)
            assert rec.ret == 1

    def test_pytest_xdist_atr_fails_session_when_test_fails(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        rec = self.inline_run("--ddtrace")
        assert rec.ret == 1

    def test_pytest_xdist_atr_passes_session_when_test_pass(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        # Create ATR-enabled settings for the main process
        from ddtrace.internal.ci_visibility._api_client import (
            TestVisibilityAPISettings,
            EarlyFlakeDetectionSettings,
            TestManagementSettings,
        )

        atr_enabled_settings = TestVisibilityAPISettings(
            coverage_enabled=False,
            skipping_enabled=False,
            require_git=False,
            itr_enabled=False,
            flaky_test_retries_enabled=True,
            known_tests_enabled=False,
            early_flake_detection=EarlyFlakeDetectionSettings(),
            test_management=TestManagementSettings(),
        )

        # Use the proper CI Visibility test environment setup
        mock_ddconfig = _get_default_civisibility_ddconfig()
        mock_ddconfig._ci_visibility_agentless_enabled = True

        with mock.patch("ddtrace.internal.ci_visibility.recorder.ddconfig", mock_ddconfig), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=atr_enabled_settings,
        ):
            extra_env = {
                "DD_API_KEY": "foobar.baz",
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
                "DD_INSTRUMENTATION_TELEMETRY_ENABLED": "0",
            }
            rec = self.inline_run("--ddtrace", extra_env=extra_env)
            assert rec.ret == 0

    def test_pytest_xdist_atr_does_not_retry_failed_setup_or_teardown(self):
        # NOTE: This feature only works for regular pytest tests. For tests inside unittest classes, setup and teardown
        # happens at the 'call' phase, and we don't have a way to detect that the error happened during setup/teardown,
        # so tests will be retried as if they were failing tests.
        # See <https://docs.pytest.org/en/8.3.x/how-to/unittest.html#pdb-unittest-note>.

        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 1

    def test_pytest_xdist_atr_junit_xml(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        rec = self.inline_run("--ddtrace", "--junit-xml=out.xml")
        assert rec.ret == 1
