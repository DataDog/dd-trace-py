"""Tests Early Flake Detection (EFD) functionality

The tests in this module only validate the behavior of EFD, so only counts and statuses of tests, retries, and sessions
are checked.

- The same known tests are used to override fetching of known tests.
- The session object is patched to never be a faulty session, by default.
"""
from unittest import mock

import pytest

from ddtrace.contrib.internal.pytest._utils import _USE_PLUGIN_V2
from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_atr
from tests.ci_visibility.util import _get_default_civisibility_ddconfig
from tests.contrib.pytest.test_pytest import PytestTestCaseBase


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
    #@pytest.fixture(autouse=True, scope="function")
    def setup_sitecustomize(self):
        sitecustomize_content = """
# sitecustomize.py
import sys
print("sitecustomize.py loaded", file=sys.stderr)
from unittest import mock
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
import ddtrace.internal.ci_visibility.recorder # Ensure parent module is loaded

_GLOBAL_SITECUSTOMIZE_PATCH_OBJECT = mock.patch(
    "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
    return_value=TestVisibilityAPISettings(flaky_test_retries_enabled=True)
)
_GLOBAL_SITECUSTOMIZE_PATCH_OBJECT.start()
"""
        self.testdir.makepyfile(sitecustomize=sitecustomize_content)

    def inline_run(self, *args, **kwargs):
        # Add -n 2 to the end of the command line arguments
        args = list(args) + ["-n", "2", "-c", "/dev/null", "--disable-warnings"]
        return super().inline_run(*args, **kwargs)

    def test_pytest_xdist_atr_no_ddtrace_does_not_retry(self):
        self.setup_sitecustomize()
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)
        rec = self.inline_run()
        assert rec.ret == 1

    def test_pytest_xdist_atr_env_var_disables_retrying(self):
        self.setup_sitecustomize()
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        with mock.patch("ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()):
            rec = self.inline_run("--ddtrace", "-s", extra_env={"DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "0"})
            assert rec.ret == 1

    def test_pytest_xdist_atr_spans(self):
        """Tests that an EFD session properly does the correct number of retries and sets the correct tags"""
        self.setup_sitecustomize()
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 1

    def test_pytest_xdist_atr_fails_session_when_test_fails(self):
        self.setup_sitecustomize()
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        rec = self.inline_run("--ddtrace")
        assert rec.ret == 1

    def test_pytest_xdist_atr_passes_session_when_test_pass(self):
        self.setup_sitecustomize()
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        rec = self.inline_run("--ddtrace")
        assert rec.ret == 0

    def test_pytest_xdist_atr_does_not_retry_failed_setup_or_teardown(self):
        # NOTE: This feature only works for regular pytest tests. For tests inside unittest classes, setup and teardown
        # happens at the 'call' phase, and we don't have a way to detect that the error happened during setup/teardown,
        # so tests will be retried as if they were failing tests.
        # See <https://docs.pytest.org/en/8.3.x/how-to/unittest.html#pdb-unittest-note>.

        self.setup_sitecustomize()
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        rec = self.inline_run("--ddtrace")
        assert rec.ret == 1

    def test_pytest_xdist_atr_junit_xml(self):
        self.setup_sitecustomize()
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        rec = self.inline_run("--ddtrace", "--junit-xml=out.xml")
        assert rec.ret == 1
