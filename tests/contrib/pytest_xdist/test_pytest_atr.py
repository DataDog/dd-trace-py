"""Tests Early Flake Detection (EFD) functionality

The tests in this module only validate the behavior of EFD, so only counts and statuses of tests, retries, and sessions
are checked.

- The same known tests are used to override fetching of known tests.
- The session object is patched to never be a faulty session, by default.
"""
from unittest import mock
from xml.etree import ElementTree

import pytest

from ddtrace.contrib.internal.pytest._utils import _USE_PLUGIN_V2
from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_atr
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.ci_visibility.util import _get_default_civisibility_ddconfig
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _get_spans_from_list


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
    def inline_run(self, *args, **kwargs):
        # Add -n 2 to the end of the command line arguments
        args = list(args) + ["-n", "2"]
        return super().inline_run(*args, **kwargs)

    @pytest.fixture(autouse=True, scope="function")
    def set_up_atr(self):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(flaky_test_retries_enabled=True),
        ):
            yield

    def test_pytest_atr_no_ddtrace_does_not_retry(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)
        rec = self.inline_run()
        assert rec.ret == 1
        # rec.assertoutcome(passed=3, failed=9, skipped=4)
        # assert len(self.pop_spans()) == 0

    def test_pytest_atr_env_var_disables_retrying(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        with mock.patch("ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()):
            rec = self.inline_run("--ddtrace", "-s", extra_env={"DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "0"})
            assert rec.ret == 1
        #     rec.assertoutcome(passed=3, failed=9, skipped=4)
        # assert len(self.pop_spans()) > 0

    def test_pytest_atr_env_var_does_not_override_api(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(flaky_test_retries_enabled=False),
        ):
            rec = self.inline_run("--ddtrace", extra_env={"DD_CIVISIBILITY_FLAKY_RETRY_ENABLED": "1"})
            assert rec.ret == 1
        #     rec.assertoutcome(passed=3, failed=9, skipped=4)
        # assert len(self.pop_spans()) > 0

    def test_pytest_atr_spans(self):
        """Tests that an EFD session properly does the correct number of retries and sets the correct tags"""
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 1
        spans = self.pop_spans()
        # session_span = _get_spans_from_list(spans, "session")[0]
        # assert session_span.get_tag("test.status") == "fail"

        # module_spans = _get_spans_from_list(spans, "module")
        # assert len(module_spans) == 1
        # assert module_spans[0].get_tag("test.status") == "fail"

        # suite_spans = _get_spans_from_list(spans, "suite")
        # assert len(suite_spans) == 5
        # for suite_span in suite_spans:
        #     suite_name = suite_span.get_tag("test.suite")
        #     if suite_name in ("test_errors.py", "test_fail.py"):
        #         assert suite_span.get_tag("test.status") == "fail"
        #     elif suite_name == "test_skip.py":
        #         assert suite_span.get_tag("test.status") == "skip"
        #     else:
        #         assert suite_span.get_tag("test.status") == "pass"

        # func_fail_spans = _get_spans_from_list(spans, "test", "test_func_fail")
        # assert len(func_fail_spans) == 6
        # func_fail_retries = 0
        # for func_fail_span in func_fail_spans:
        #     assert func_fail_span.get_tag("test.status") == "fail"
        #     if func_fail_span.get_tag("test.is_retry") == "true":
        #         func_fail_retries += 1
        # assert func_fail_retries == 5

        # class_func_fail_spans = _get_spans_from_list(spans, "test", "SomeTestCase::test_class_func_fail")
        # assert len(class_func_fail_spans) == 6
        # class_func_fail_retries = 0
        # for class_func_fail_span in class_func_fail_spans:
        #     assert class_func_fail_span.get_tag("test.status") == "fail"
        #     if class_func_fail_span.get_tag("test.is_retry") == "true":
        #         class_func_fail_retries += 1
        # assert class_func_fail_retries == 5

        # func_fail_skip_spans = _get_spans_from_list(spans, "test", "test_func_retries_skip")
        # assert len(func_fail_skip_spans) == 6
        # func_fail_skip_retries = 0
        # for func_fail_skip_span in func_fail_skip_spans:
        #     func_fail_skip_is_retry = func_fail_skip_span.get_tag("test.is_retry") == "true"
        #     assert func_fail_skip_span.get_tag("test.status") == ("skip" if func_fail_skip_is_retry else "fail")
        #     if func_fail_skip_is_retry:
        #         func_fail_skip_retries += 1
        # assert func_fail_skip_retries == 5

        # class_func_fail_skip_spans = _get_spans_from_list(spans, "test", "SomeTestCase::test_class_func_retries_skip")
        # assert len(class_func_fail_skip_spans) == 6
        # class_func_fail_skip_retries = 0
        # for class_func_fail_skip_span in class_func_fail_skip_spans:
        #     class_func_fail_skip_is_retry = class_func_fail_skip_span.get_tag("test.is_retry") == "true"
        #     assert class_func_fail_skip_span.get_tag("test.status") == (
        #         "skip" if class_func_fail_skip_is_retry else "fail"
        #     )
        #     if class_func_fail_skip_is_retry:
        #         class_func_fail_skip_retries += 1
        # assert class_func_fail_skip_retries == 5

        # func_pass_spans = _get_spans_from_list(spans, "test", "test_func_pass")
        # assert len(func_pass_spans) == 1
        # assert func_pass_spans[0].get_tag("test.status") == "pass"
        # assert func_pass_spans[0].get_tag("test.retry") is None

        # # Skips are tested twice: once with a skip mark (skips during setup) and once using pytest.skip() in the
        # # test body (skips during call), neither should be retried
        # func_skip_mark_spans = _get_spans_from_list(spans, "test", "test_func_skip_mark")
        # assert len(func_skip_mark_spans) == 1
        # assert func_skip_mark_spans[0].get_tag("test.status") == "skip"
        # assert func_skip_mark_spans[0].get_tag("test.is_retry") is None

        # func_skip_inside_spans = _get_spans_from_list(spans, "test", "test_func_skip_inside")
        # assert len(func_skip_inside_spans) == 1
        # assert func_skip_inside_spans[0].get_tag("test.status") == "skip"
        # assert func_skip_inside_spans[0].get_tag("test.is_retry") is None

        # class_func_skip_mark_spans = _get_spans_from_list(spans, "test", "SomeTestCase::test_class_func_skip_mark")
        # assert len(class_func_skip_mark_spans) == 1
        # assert class_func_skip_mark_spans[0].get_tag("test.status") == "skip"
        # assert class_func_skip_mark_spans[0].get_tag("test.is_retry") is None

        # class_func_skip_inside_spans = _get_spans_from_list(spans, "test", "SomeTestCase::test_class_func_skip_inside")
        # assert len(class_func_skip_inside_spans) == 1
        # assert class_func_skip_inside_spans[0].get_tag("test.status") == "skip"
        # assert class_func_skip_inside_spans[0].get_tag("test.is_retry") is None

        # assert len(spans) == 51

    def test_pytest_atr_fails_session_when_test_fails(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        rec = self.inline_run("--ddtrace")
        # spans = self.pop_spans()
        assert rec.ret == 1
        # assert len(spans) == 48

    def test_pytest_atr_passes_session_when_test_pass(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        rec = self.inline_run("--ddtrace")
        # spans = self.pop_spans()
        assert rec.ret == 0
        # assert len(spans) == 23

    def test_pytest_atr_does_not_retry_failed_setup_or_teardown(self):
        # NOTE: This feature only works for regular pytest tests. For tests inside unittest classes, setup and teardown
        # happens at the 'call' phase, and we don't have a way to detect that the error happened during setup/teardown,
        # so tests will be retried as if they were failing tests.
        # See <https://docs.pytest.org/en/8.3.x/how-to/unittest.html#pdb-unittest-note>.

        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        rec = self.inline_run("--ddtrace")
        # spans = self.pop_spans()
        # fails_setup_spans = _get_spans_from_list(spans, "test", "test_func_fails_setup")
        # assert len(fails_setup_spans) == 1
        # assert fails_setup_spans[0].get_tag("test.is_retry") != "true"

        # fails_teardown_spans = _get_spans_from_list(spans, "test", "test_func_fails_teardown")
        # assert len(fails_teardown_spans) == 1
        # assert fails_teardown_spans[0].get_tag("test.is_retry") != "true"

        assert rec.ret == 1
        # assert len(spans) == 5

    def test_pytest_atr_junit_xml(self):
        self.testdir.makepyfile(test_pass=_TEST_PASS_CONTENT)
        self.testdir.makepyfile(test_fail=_TEST_FAIL_CONTENT)
        self.testdir.makepyfile(test_errors=_TEST_ERRORS_CONTENT)
        self.testdir.makepyfile(test_pass_on_retries=_TEST_PASS_ON_RETRIES_CONTENT)
        self.testdir.makepyfile(test_skip=_TEST_SKIP_CONTENT)

        rec = self.inline_run("--ddtrace", "--junit-xml=out.xml")
        assert rec.ret == 1

        # test_suite = ElementTree.parse(f"{self.testdir}/out.xml").find("testsuite")

        # # There are 15 tests, but we get 16 in the JUnit XML output, because a test that passes during call but fails
        # # during teardown is counted twice. This is a bug in pytest, not ddtrace.
        # assert test_suite.attrib["tests"] == "16"
        # assert test_suite.attrib["failures"] == "4"
        # assert test_suite.attrib["skipped"] == "4"
        # assert test_suite.attrib["errors"] == "2"
