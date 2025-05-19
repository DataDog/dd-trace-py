import functools
import sys
import unittest
from unittest import mock

import pytest

from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.constants import ERROR_MSG
from ddtrace.constants import ERROR_TYPE
from ddtrace.constants import SPAN_KIND
from ddtrace.contrib.internal.unittest.constants import COMPONENT_VALUE
from ddtrace.contrib.internal.unittest.constants import FRAMEWORK
from ddtrace.contrib.internal.unittest.constants import KIND
from ddtrace.contrib.internal.unittest.constants import MODULE_OPERATION_NAME
from ddtrace.contrib.internal.unittest.constants import SESSION_OPERATION_NAME
from ddtrace.contrib.internal.unittest.constants import SUITE_OPERATION_NAME
from ddtrace.contrib.internal.unittest.constants import TEST_OPERATION_NAME
from ddtrace.contrib.internal.unittest.patch import _set_tracer
from ddtrace.contrib.internal.unittest.patch import patch
from ddtrace.ext import SpanTypes
from ddtrace.ext import test
from ddtrace.ext.ci import RUNTIME_VERSION
from ddtrace.ext.ci import _get_runtime_and_os_metadata
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.constants import MODULE_ID
from ddtrace.internal.ci_visibility.constants import SESSION_ID
from ddtrace.internal.ci_visibility.constants import SUITE_ID
from ddtrace.internal.constants import COMPONENT
from tests.utils import TracerTestCase
from tests.utils import override_env


def _disable_ci_visibility(f):
    """Decorator to disable CI Visibility after a test finishes."""
    # DEV: The tests here enable CI Visibility to test the `unittest` plugin, but this causes CI Visibility to be
    # enabled _also_ for the pytest session that is running the tests. Disabling it in a fixture is not enough because
    # the fixture only gets to run at teardown, by which time pytest has already tried to run some hooks (such as
    # pytest_runtest_makereport) with CI Visibility enabled, but the session start hooks did not run because CI
    # Visibility was disabled at the beginning of the session, causing "No session exists" errors. Therefore we need to
    # disable CI Visibility right after the test body runs, _before_ control returns to pytest.
    @functools.wraps(f)
    def wrapper(*args, **kwargs):
        try:
            return f(*args, **kwargs)
        finally:
            if CIVisibility.enabled:
                CIVisibility.disable()

    return wrapper


class UnittestTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    @pytest.fixture(scope="function", autouse=True)
    def _patch_and_override_env(self):
        with override_env(dict(DD_API_KEY="notanapikey")):
            patch()
            yield

    @pytest.fixture(autouse=True)
    def _dummy_check_enabled_features(self):
        """By default, assume that _check_enabled_features() returns an ITR-disabled response.

        Tests that need a different response should re-patch the CIVisibility object.
        """
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(False, False, False, False),
        ):
            yield

    @_disable_ci_visibility
    def test_unittest_set_test_session_name(self):
        """Check that the unittest command is used to set the test session name."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_pass_first(self):
                self.assertTrue(2 == 2)

        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility.set_test_session_name"
        ) as set_test_session_name_mock:
            suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
            unittest.TextTestRunner(verbosity=0).run(suite)

        set_test_session_name_mock.assert_called_once_with(test_command="python -m unittest")

    @_disable_ci_visibility
    def test_unittest_pass_single(self):
        """Test with a `unittest` test which should pass."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_pass_first(self):
                self.assertTrue(2 == 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 4

        test_suite_span = spans[2]
        test_module_span = spans[1]
        test_session_span = spans[0]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(test.SKIP_REASON) is None
            assert spans[i].get_tag(test.TEST_STATUS) == test.Status.PASS.value
            assert spans[i].get_tag(test.COMMAND) == "python -m unittest"
            assert spans[i].get_tag(ERROR_MSG) is None
            assert spans[i].get_tag(ERROR_TYPE) is None

        assert spans[3].name == TEST_OPERATION_NAME
        assert spans[3].get_tag(test.NAME) == "test_will_pass_first"
        assert spans[3].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[3].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[3].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[3].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_suite_span.name == SUITE_OPERATION_NAME
        assert test_suite_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_suite_span.get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert test_suite_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_suite_span.get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_module_span.name == MODULE_OPERATION_NAME
        assert test_module_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_module_span.get_tag(test.SUITE) is None
        assert test_module_span.get_tag(MODULE_ID) == str(test_module_span.span_id)

        assert test_session_span.name == SESSION_OPERATION_NAME
        assert test_session_span.get_tag(test.MODULE) is None
        assert test_session_span.get_tag(test.SUITE) is None

    @_disable_ci_visibility
    def test_unittest_pass_multiple(self):
        """Tests with`unittest` tests which should pass."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_pass_first(self):
                self.assertTrue(2 == 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

            def test_will_pass_second(self):
                self.assertTrue(3 == 3)
                self.assertTrue(4 != 5)

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 5

        test_suite_span = spans[2]
        test_module_span = spans[1]
        test_session_span = spans[0]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(test.COMMAND) == "python -m unittest"
            assert spans[i].get_tag(test.TEST_STATUS) == test.Status.PASS.value
            assert spans[i].get_tag(test.SKIP_REASON) is None
            assert spans[i].get_tag(ERROR_MSG) is None
            assert spans[i].get_tag(ERROR_TYPE) is None

        assert spans[3].name == TEST_OPERATION_NAME
        assert spans[3].get_tag(test.NAME) == "test_will_pass_first"
        assert spans[3].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[3].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[3].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[3].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert spans[4].name == TEST_OPERATION_NAME
        assert spans[4].get_tag(test.NAME) == "test_will_pass_second"
        assert spans[4].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[4].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[4].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[4].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_suite_span.name == SUITE_OPERATION_NAME
        assert test_suite_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_suite_span.get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert test_suite_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_suite_span.get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_module_span.name == MODULE_OPERATION_NAME
        assert test_module_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_module_span.get_tag(test.SUITE) is None
        assert test_module_span.get_tag(MODULE_ID) == str(test_module_span.span_id)

        assert test_session_span.name == SESSION_OPERATION_NAME
        assert test_session_span.get_tag(test.MODULE) is None
        assert test_session_span.get_tag(test.SUITE) is None

    @pytest.mark.skipif(
        sys.version_info[0] <= 3 and sys.version_info[1] <= 7, reason="Triggers a bug with skip reason being empty"
    )
    @_disable_ci_visibility
    def test_unittest_skip_single_no_reason(self):
        """Tests with a `unittest` test which should be skipped."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            @unittest.skip
            def test_will_be_skipped(self):
                self.assertTrue(2 == 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 4

        test_suite_span = spans[2]
        test_module_span = spans[1]
        test_session_span = spans[0]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(test.COMMAND) == "python -m unittest"
            assert spans[i].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
            assert spans[i].get_tag(ERROR_MSG) is None
            assert spans[i].get_tag(ERROR_TYPE) is None

        assert spans[3].name == TEST_OPERATION_NAME
        assert spans[3].get_tag(test.NAME) == "test_will_be_skipped"
        assert spans[3].get_tag(test.SKIP_REASON) == ""
        assert spans[3].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[3].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[3].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[3].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_suite_span.name == SUITE_OPERATION_NAME
        assert test_suite_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_suite_span.get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert test_suite_span.get_tag(test.SKIP_REASON) is None
        assert test_suite_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_suite_span.get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_module_span.name == MODULE_OPERATION_NAME
        assert test_module_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_module_span.get_tag(test.SUITE) is None
        assert test_module_span.get_tag(test.SKIP_REASON) is None
        assert test_module_span.get_tag(MODULE_ID) == str(test_module_span.span_id)

        assert test_session_span.name == SESSION_OPERATION_NAME
        assert test_session_span.get_tag(test.MODULE) is None
        assert test_session_span.get_tag(test.SUITE) is None
        assert test_session_span.get_tag(test.SKIP_REASON) is None

    @_disable_ci_visibility
    def test_unittest_skip_single_reason(self):
        """Tests with a `unittest` test which should be skipped."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            @unittest.skip("demonstrating skipping with a reason")
            def test_will_be_skipped(self):
                self.assertTrue(2 == 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 4

        test_suite_span = spans[2]
        test_module_span = spans[1]
        test_session_span = spans[0]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(test.COMMAND) == "python -m unittest"
            assert spans[i].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
            assert spans[i].get_tag(ERROR_MSG) is None
            assert spans[i].get_tag(ERROR_TYPE) is None

        assert spans[3].name == TEST_OPERATION_NAME
        assert spans[3].get_tag(test.NAME) == "test_will_be_skipped"
        assert spans[3].get_tag(test.SKIP_REASON) == "demonstrating skipping with a reason"
        assert spans[3].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[3].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[3].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[3].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_suite_span.name == SUITE_OPERATION_NAME
        assert test_suite_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_suite_span.get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert test_suite_span.get_tag(test.SKIP_REASON) is None
        assert test_suite_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_suite_span.get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_module_span.name == MODULE_OPERATION_NAME
        assert test_module_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_module_span.get_tag(test.SUITE) is None
        assert test_module_span.get_tag(test.SKIP_REASON) is None
        assert test_module_span.get_tag(MODULE_ID) == str(test_module_span.span_id)

        assert test_session_span.name == SESSION_OPERATION_NAME
        assert test_session_span.get_tag(test.MODULE) is None
        assert test_session_span.get_tag(test.SUITE) is None
        assert test_session_span.get_tag(test.SKIP_REASON) is None

    @_disable_ci_visibility
    def test_unittest_skip_multiple_reason(self):
        """Test with `unittest` tests which should be skipped."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            @unittest.skip("demonstrating skipping with a reason")
            def test_will_be_skipped_first(self):
                self.assertTrue(2 == 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

            @unittest.skip("demonstrating skipping with a different reason")
            def test_will_be_skipped_second(self):
                self.assertTrue(2 == 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 5

        test_suite_span = spans[2]
        test_module_span = spans[1]
        test_session_span = spans[0]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(test.COMMAND) == "python -m unittest"
            assert spans[i].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
            assert spans[i].get_tag(ERROR_MSG) is None
            assert spans[i].get_tag(ERROR_TYPE) is None

        assert spans[3].name == TEST_OPERATION_NAME
        assert spans[3].get_tag(test.NAME) == "test_will_be_skipped_first"
        assert spans[3].get_tag(test.SKIP_REASON) == "demonstrating skipping with a reason"
        assert spans[3].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[3].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[3].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[3].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert spans[4].name == TEST_OPERATION_NAME
        assert spans[4].get_tag(test.NAME) == "test_will_be_skipped_second"
        assert spans[4].get_tag(test.SKIP_REASON) == "demonstrating skipping with a different reason"
        assert spans[4].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[4].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[4].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[4].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_suite_span.name == SUITE_OPERATION_NAME
        assert test_suite_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_suite_span.get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert test_suite_span.get_tag(test.SKIP_REASON) is None
        assert test_suite_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_suite_span.get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_module_span.name == MODULE_OPERATION_NAME
        assert test_module_span.get_tag(test.COMMAND) == "python -m unittest"
        assert test_module_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_module_span.get_tag(test.SUITE) is None
        assert test_module_span.get_tag(test.SKIP_REASON) is None
        assert test_module_span.get_tag(MODULE_ID) == str(test_module_span.span_id)

        assert test_session_span.name == SESSION_OPERATION_NAME
        assert test_session_span.get_tag(test.COMMAND) == "python -m unittest"
        assert test_session_span.get_tag(test.MODULE) is None
        assert test_session_span.get_tag(test.SUITE) is None
        assert test_session_span.get_tag(test.SKIP_REASON) is None

    @pytest.mark.skipif(
        sys.version_info[0] <= 3 and sys.version_info[1] <= 7, reason="Triggers a bug with skip reason being empty"
    )
    @_disable_ci_visibility
    def test_unittest_skip_combined(self):
        """Test with `unittest` tests which should be skipped."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            @unittest.skip
            def test_will_be_skipped(self):
                self.assertTrue(2 == 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

            def test_wont_be_skipped(self):
                self.assertTrue(2 == 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 5

        test_suite_span = spans[2]
        test_module_span = spans[1]
        test_session_span = spans[0]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(test.COMMAND) == "python -m unittest"
            assert spans[i].get_tag(ERROR_MSG) is None
            assert spans[i].get_tag(ERROR_TYPE) is None

        assert spans[3].name == TEST_OPERATION_NAME
        assert spans[3].get_tag(test.NAME) == "test_will_be_skipped"
        assert spans[3].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
        assert spans[3].get_tag(test.SKIP_REASON) == ""
        assert spans[3].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[3].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[3].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[3].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert spans[4].name == TEST_OPERATION_NAME
        assert spans[4].get_tag(test.NAME) == "test_wont_be_skipped"
        assert spans[4].get_tag(test.TEST_STATUS) == test.Status.PASS.value
        assert spans[4].get_tag(test.SKIP_REASON) is None
        assert spans[4].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[4].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[4].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[4].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_suite_span.name == SUITE_OPERATION_NAME
        assert test_suite_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_suite_span.get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert test_suite_span.get_tag(test.TEST_STATUS) == test.Status.PASS.value
        assert test_suite_span.get_tag(test.SKIP_REASON) is None
        assert test_suite_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_suite_span.get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_module_span.name == MODULE_OPERATION_NAME
        assert test_module_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_module_span.get_tag(test.SUITE) is None
        assert test_module_span.get_tag(test.TEST_STATUS) == test.Status.PASS.value
        assert test_module_span.get_tag(test.SKIP_REASON) is None
        assert test_module_span.get_tag(MODULE_ID) == str(test_module_span.span_id)

        assert test_session_span.name == SESSION_OPERATION_NAME
        assert test_session_span.get_tag(test.MODULE) is None
        assert test_session_span.get_tag(test.SUITE) is None
        assert test_session_span.get_tag(test.TEST_STATUS) == test.Status.PASS.value
        assert test_session_span.get_tag(test.SKIP_REASON) is None

    @_disable_ci_visibility
    def test_unittest_fail_single(self):
        """Test with `unittest` tests which should fail."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_fail(self):
                self.assertTrue(2 != 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 4

        test_suite_span = spans[2]
        test_module_span = spans[1]
        test_session_span = spans[0]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(test.TEST_STATUS) == test.Status.FAIL.value
            assert spans[i].get_tag(test.SKIP_REASON) is None

        assert spans[3].name == TEST_OPERATION_NAME
        assert spans[3].get_tag(test.NAME) == "test_will_fail"
        assert spans[3].get_tag(ERROR_MSG) == "False is not true"
        assert spans[3].get_tag(ERROR_TYPE) == "builtins.AssertionError"
        assert spans[3].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[3].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[3].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[3].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_suite_span.name == SUITE_OPERATION_NAME
        assert test_suite_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_suite_span.get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert test_suite_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_suite_span.get_tag(SUITE_ID) == str(test_suite_span.span_id)
        assert test_suite_span.get_tag(ERROR_MSG) is None
        assert test_suite_span.get_tag(ERROR_TYPE) is None

        assert test_module_span.name == MODULE_OPERATION_NAME
        assert test_module_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_module_span.get_tag(test.SUITE) is None
        assert test_module_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_module_span.get_tag(ERROR_MSG) is None
        assert test_module_span.get_tag(ERROR_TYPE) is None

        assert test_session_span.name == SESSION_OPERATION_NAME
        assert test_session_span.get_tag(test.MODULE) is None
        assert test_session_span.get_tag(test.SUITE) is None
        assert test_session_span.get_tag(ERROR_MSG) is None
        assert test_session_span.get_tag(ERROR_TYPE) is None

    @_disable_ci_visibility
    def test_unittest_fail_multiple(self):
        """Test with `unittest` tests which should fail."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_fail_first(self):
                self.assertTrue(2 != 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

            def test_will_fail_second(self):
                self.assertTrue(2 != 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()

        assert len(spans) == 5

        test_suite_span = spans[2]
        test_module_span = spans[1]
        test_session_span = spans[0]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(test.TEST_STATUS) == test.Status.FAIL.value
            assert spans[i].get_tag(test.SKIP_REASON) is None
            assert spans[i].get_tag(test.COMMAND) == "python -m unittest"

        assert spans[3].name == TEST_OPERATION_NAME
        assert spans[3].get_tag(test.NAME) == "test_will_fail_first"
        assert spans[3].get_tag(ERROR_MSG) == "False is not true"
        assert spans[3].get_tag(ERROR_TYPE) == "builtins.AssertionError"
        assert spans[3].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[3].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[3].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[3].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert spans[4].name == TEST_OPERATION_NAME
        assert spans[4].get_tag(test.NAME) == "test_will_fail_second"
        assert spans[4].get_tag(ERROR_MSG) == "False is not true"
        assert spans[4].get_tag(ERROR_TYPE) == "builtins.AssertionError"
        assert spans[4].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[4].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[4].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[4].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert test_suite_span.name == SUITE_OPERATION_NAME
        assert test_suite_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_suite_span.get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert test_suite_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_suite_span.get_tag(SUITE_ID) == str(test_suite_span.span_id)
        assert test_suite_span.get_tag(ERROR_MSG) is None
        assert test_suite_span.get_tag(ERROR_TYPE) is None

        assert test_module_span.name == MODULE_OPERATION_NAME
        assert test_module_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_module_span.get_tag(test.SUITE) is None
        assert test_module_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_module_span.get_tag(ERROR_MSG) is None
        assert test_module_span.get_tag(ERROR_TYPE) is None

        assert test_session_span.name == SESSION_OPERATION_NAME
        assert test_session_span.get_tag(test.MODULE) is None
        assert test_session_span.get_tag(test.SUITE) is None
        assert test_session_span.get_tag(ERROR_MSG) is None
        assert test_session_span.get_tag(ERROR_TYPE) is None

    @_disable_ci_visibility
    def test_unittest_combined(self):
        """Test with `unittest` tests which pass, get skipped and fail combined."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_pass_first(self):
                self.assertTrue(2 == 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

            def test_will_fail_first(self):
                self.assertTrue(2 != 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

            @unittest.skip("another skip reason")
            def test_will_be_skipped_with_a_reason(self):
                self.assertTrue(2 == 2)
                self.assertTrue("test string" == "test string")
                self.assertFalse("not equal to" == "this")

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 6

        test_session_span = spans[0]
        test_module_span = spans[1]
        test_suite_span = spans[2]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(test.COMMAND) == "python -m unittest"

        assert spans[3].name == TEST_OPERATION_NAME
        assert spans[3].get_tag(test.NAME) == "test_will_be_skipped_with_a_reason"
        assert spans[3].get_tag(test.TEST_STATUS) == test.Status.SKIP.value
        assert spans[3].get_tag(test.SKIP_REASON) == "another skip reason"
        assert spans[3].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[3].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[3].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[3].get_tag(SUITE_ID) == str(test_suite_span.span_id)
        assert spans[3].get_tag(ERROR_MSG) is None
        assert spans[3].get_tag(ERROR_TYPE) is None

        assert spans[4].name == TEST_OPERATION_NAME
        assert spans[4].get_tag(test.NAME) == "test_will_fail_first"
        assert spans[4].get_tag(test.TEST_STATUS) == test.Status.FAIL.value
        assert spans[4].get_tag(test.SKIP_REASON) is None
        assert spans[4].get_tag(ERROR_MSG) == "False is not true"
        assert spans[4].get_tag(ERROR_TYPE) == "builtins.AssertionError"
        assert spans[4].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[4].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[4].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[4].get_tag(SUITE_ID) == str(test_suite_span.span_id)

        assert spans[5].name == TEST_OPERATION_NAME
        assert spans[5].get_tag(test.NAME) == "test_will_pass_first"
        assert spans[5].get_tag(test.TEST_STATUS) == test.Status.PASS.value
        assert spans[5].get_tag(test.SKIP_REASON) is None
        assert spans[5].get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert spans[5].get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert spans[5].get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert spans[5].get_tag(SUITE_ID) == str(test_suite_span.span_id)
        assert spans[5].get_tag(ERROR_MSG) is None
        assert spans[5].get_tag(ERROR_TYPE) is None

        assert test_suite_span.name == SUITE_OPERATION_NAME
        assert test_suite_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_suite_span.get_tag(test.SUITE) == "UnittestExampleTestCase"
        assert test_suite_span.get_tag(test.TEST_STATUS) == test.Status.FAIL.value
        assert test_suite_span.get_tag(test.SKIP_REASON) is None
        assert test_suite_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_suite_span.get_tag(SUITE_ID) == str(test_suite_span.span_id)
        assert test_suite_span.get_tag(ERROR_MSG) is None
        assert test_suite_span.get_tag(ERROR_TYPE) is None

        assert test_module_span.name == MODULE_OPERATION_NAME
        assert test_module_span.get_tag(test.MODULE) == "tests.contrib.unittest.test_unittest"
        assert test_module_span.get_tag(test.SUITE) is None
        assert test_module_span.get_tag(test.TEST_STATUS) == test.Status.FAIL.value
        assert test_module_span.get_tag(test.SKIP_REASON) is None
        assert test_module_span.get_tag(MODULE_ID) == str(test_module_span.span_id)
        assert test_module_span.get_tag(ERROR_MSG) is None
        assert test_module_span.get_tag(ERROR_TYPE) is None

        assert test_session_span.name == SESSION_OPERATION_NAME
        assert test_session_span.get_tag(test.MODULE) is None
        assert test_session_span.get_tag(test.SUITE) is None
        assert test_session_span.get_tag(test.TEST_STATUS) == test.Status.FAIL.value
        assert test_session_span.get_tag(test.SKIP_REASON) is None
        assert test_session_span.get_tag(ERROR_MSG) is None
        assert test_session_span.get_tag(ERROR_TYPE) is None

    @_disable_ci_visibility
    def test_unittest_nested_test_cases(self):
        """Test with `unittest` test cases which pass, get skipped and fail combined."""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            class SubTest1(unittest.TestCase):
                def test_subtest1_will_pass_first(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                def test_subtest1_will_fail_first(self):
                    self.assertTrue(2 != 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                @unittest.skip("another skip reason for subtest1")
                def test_subtest1_will_be_skipped_with_a_reason(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

            class SubTest2(unittest.TestCase):
                def test_subtest2_will_pass(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                @unittest.skip("another skip reason for subtest2")
                def test_subtest2_will_be_skipped_with_a_reason(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase.SubTest1)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(UnittestExampleTestCase.SubTest2))
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 9

        test_session_span = spans[0]
        test_module_span = spans[1]
        test_suite_span_subtest_1 = spans[2]
        test_suite_span_subtest_2 = spans[6]

        expected_result = [
            {
                "name": SESSION_OPERATION_NAME,
                test.TEST_STATUS: test.Status.FAIL.value,
            },
            {
                "name": MODULE_OPERATION_NAME,
                test.TEST_STATUS: test.Status.FAIL.value,
                test.MODULE: "tests.contrib.unittest.test_unittest",
                MODULE_ID: str(test_module_span.span_id),
            },
            {
                "name": SUITE_OPERATION_NAME,
                test.TEST_STATUS: test.Status.FAIL.value,
                test.SUITE: "SubTest1",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_1.span_id),
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest1_will_be_skipped_with_a_reason",
                test.TEST_STATUS: test.Status.SKIP.value,
                test.SKIP_REASON: "another skip reason for subtest1",
                test.SUITE: "SubTest1",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_1.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest1_will_fail_first",
                test.TEST_STATUS: test.Status.FAIL.value,
                test.SUITE: "SubTest1",
                ERROR_MSG: "False is not true",
                ERROR_TYPE: "builtins.AssertionError",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_1.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest1_will_pass_first",
                test.TEST_STATUS: test.Status.PASS.value,
                test.SUITE: "SubTest1",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_1.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
            {
                "name": SUITE_OPERATION_NAME,
                test.TEST_STATUS: test.Status.PASS.value,
                test.SUITE: "SubTest2",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_2.span_id),
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest2_will_be_skipped_with_a_reason",
                test.TEST_STATUS: test.Status.SKIP.value,
                test.SKIP_REASON: "another skip reason for subtest2",
                test.SUITE: "SubTest2",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_2.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest2_will_pass",
                test.TEST_STATUS: test.Status.PASS.value,
                test.SUITE: "SubTest2",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_2.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
        ]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(test.TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.COMMAND) == "python -m unittest"
            assert spans[i].get_tag(test.NAME) == expected_result[i].get(test.NAME, None)
            assert spans[i].get_tag(test.TEST_STATUS) == expected_result[i].get(test.TEST_STATUS, None)
            assert spans[i].get_tag(test.SKIP_REASON) == expected_result[i].get(test.SKIP_REASON, None)
            assert spans[i].get_tag(test.SUITE) == expected_result[i].get(test.SUITE, None)
            assert spans[i].get_tag(ERROR_MSG) == expected_result[i].get(ERROR_MSG, None)
            assert spans[i].get_tag(ERROR_TYPE) == expected_result[i].get(ERROR_TYPE, None)
            assert spans[i].name == expected_result[i].get("name", None)
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(MODULE_ID) == expected_result[i].get(MODULE_ID, None)
            assert spans[i].get_tag(SUITE_ID) == expected_result[i].get(SUITE_ID, None)
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]

    @_disable_ci_visibility
    def test_unittest_xfail_xpass(self):
        """Test with `unittest` test cases which pass, get skipped, xfail and xpass"""
        _set_tracer(self.tracer)

        class UnittestExampleTestCase(unittest.TestCase):
            class SubTest1(unittest.TestCase):
                def test_subtest1_will_pass_first(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                def test_subtest1_will_fail_first(self):
                    self.assertTrue(2 != 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

                @unittest.skip("another skip reason for subtest1")
                def test_subtest1_will_be_skipped_with_a_reason(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

            class SubTest2(unittest.TestCase):
                @unittest.expectedFailure
                def test_subtest2_will_xfail(self):
                    int("hello")

                @unittest.expectedFailure
                def test_subtest2_will_not_xfail(self):
                    int("3")

                @unittest.skip("another skip reason for subtest2")
                def test_subtest2_will_be_skipped_with_a_reason(self):
                    self.assertTrue(2 == 2)
                    self.assertTrue("test string" == "test string")
                    self.assertFalse("not equal to" == "this")

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase.SubTest1)
        suite.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(UnittestExampleTestCase.SubTest2))
        unittest.TextTestRunner(verbosity=0).run(suite)

        spans = self.pop_spans()
        assert len(spans) == 10

        test_session_span = spans[0]
        test_module_span = spans[1]
        test_suite_span_subtest_1 = spans[2]
        test_suite_span_subtest_2 = spans[6]

        expected_result = [
            {
                "name": SESSION_OPERATION_NAME,
                test.TEST_STATUS: test.Status.FAIL.value,
            },
            {
                "name": MODULE_OPERATION_NAME,
                test.TEST_STATUS: test.Status.FAIL.value,
                test.MODULE: "tests.contrib.unittest.test_unittest",
                MODULE_ID: str(test_module_span.span_id),
            },
            {
                "name": SUITE_OPERATION_NAME,
                test.TEST_STATUS: test.Status.FAIL.value,
                test.SUITE: "SubTest1",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_1.span_id),
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest1_will_be_skipped_with_a_reason",
                test.TEST_STATUS: test.Status.SKIP.value,
                test.SKIP_REASON: "another skip reason for subtest1",
                test.SUITE: "SubTest1",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_1.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest1_will_fail_first",
                test.TEST_STATUS: test.Status.FAIL.value,
                test.SUITE: "SubTest1",
                ERROR_MSG: "False is not true",
                ERROR_TYPE: "builtins.AssertionError",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_1.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest1_will_pass_first",
                test.TEST_STATUS: test.Status.PASS.value,
                test.SUITE: "SubTest1",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_1.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
            {
                "name": SUITE_OPERATION_NAME,
                test.TEST_STATUS: test.Status.FAIL.value,
                test.SUITE: "SubTest2",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_2.span_id),
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest2_will_be_skipped_with_a_reason",
                test.TEST_STATUS: test.Status.SKIP.value,
                test.SKIP_REASON: "another skip reason for subtest2",
                test.SUITE: "SubTest2",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_2.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest2_will_not_xfail",
                test.TEST_STATUS: test.Status.FAIL.value,
                test.RESULT: test.Status.XPASS.value,
                test.SUITE: "SubTest2",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_2.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
            {
                "name": TEST_OPERATION_NAME,
                test.NAME: "test_subtest2_will_xfail",
                test.TEST_STATUS: test.Status.PASS.value,
                test.RESULT: test.Status.XFAIL.value,
                test.SUITE: "SubTest2",
                MODULE_ID: str(test_module_span.span_id),
                SUITE_ID: str(test_suite_span_subtest_2.span_id),
                test.MODULE: "tests.contrib.unittest.test_unittest",
            },
        ]

        for i in range(len(spans)):
            assert spans[i].get_tag(test.TEST_TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.TEST_FRAMEWORK) == FRAMEWORK
            assert spans[i].get_tag(SPAN_KIND) == KIND
            assert spans[i].get_tag(COMPONENT) == COMPONENT_VALUE
            assert spans[i].get_tag(test.TYPE) == SpanTypes.TEST
            assert spans[i].get_tag(test.COMMAND) == "python -m unittest"
            assert spans[i].get_tag(test.NAME) == expected_result[i].get(test.NAME, None)
            assert spans[i].get_tag(test.TEST_STATUS) == expected_result[i].get(test.TEST_STATUS, None)
            assert spans[i].get_tag(test.TEST_RESULT) == expected_result[i].get(test.TEST_RESULT, None)
            assert spans[i].get_tag(test.SKIP_REASON) == expected_result[i].get(test.SKIP_REASON, None)
            assert spans[i].get_tag(test.SUITE) == expected_result[i].get(test.SUITE, None)
            assert spans[i].get_tag(ERROR_MSG) == expected_result[i].get(ERROR_MSG, None)
            assert spans[i].get_tag(ERROR_TYPE) == expected_result[i].get(ERROR_TYPE, None)
            assert spans[i].name == expected_result[i].get("name", None)
            assert spans[i].get_tag(SESSION_ID) == str(test_session_span.span_id)
            assert spans[i].get_tag(MODULE_ID) == expected_result[i].get(MODULE_ID, None)
            assert spans[i].get_tag(SUITE_ID) == expected_result[i].get(SUITE_ID, None)
            assert spans[i].get_tag(test.FRAMEWORK_VERSION) == _get_runtime_and_os_metadata()[RUNTIME_VERSION]
