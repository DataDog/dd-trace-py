from unittest import mock

import pytest

from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _get_spans_from_list


_TEST_CONTENT = """
import flaky

def test_func_pass():
    assert True

def test_func_fail():
    assert False

flaky_counter = 0

@flaky.flaky
def test_func_flaky():
    global flaky_counter
    flaky_counter += 1
    assert flaky_counter >= 2

"""


class TestPytestFlakyPlugin(PytestTestCaseBase):
    """
    Check that the Test Optimization pytest plugin interacts correctly with the `flaky` plugin.
    """

    @pytest.fixture(autouse=True, scope="function")
    def set_up_atr(self):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(flaky_test_retries_enabled=True),
        ):
            yield

    def test_pytest_flaky(self):
        self.testdir.makepyfile(test_sample=_TEST_CONTENT)
        rec = self.inline_run("--ddtrace", "-p", "flaky")
        spans = self.pop_spans()
        pass_spans = _get_spans_from_list(spans, "test", "test_func_pass")
        fail_spans = _get_spans_from_list(spans, "test", "test_func_fail")
        flaky_spans = _get_spans_from_list(spans, "test", "test_func_flaky")
        assert len(pass_spans) == 1
        assert len(fail_spans) == 1  # ATR is off because the `flaky` plugin is enabled
        assert len(flaky_spans) == 1  # ATR is off because the `flaky` plugin is enabled
        assert pass_spans[0].get_tag("test.status") == "pass"
        assert fail_spans[0].get_tag("test.status") == "fail"
        assert flaky_spans[0].get_tag("test.status") == "pass"  # `flaky` plugin made it pass
        assert rec.ret == 1

    def test_pytest_no_flaky(self):
        self.testdir.makepyfile(test_sample=_TEST_CONTENT)
        rec = self.inline_run("--ddtrace", "-p", "no:flaky")
        spans = self.pop_spans()
        pass_spans = _get_spans_from_list(spans, "test", "test_func_pass")
        fail_spans = _get_spans_from_list(spans, "test", "test_func_fail")
        flaky_spans = _get_spans_from_list(spans, "test", "test_func_flaky")
        assert len(pass_spans) == 1
        assert len(fail_spans) == 6  # ATR is on
        assert len(flaky_spans) == 2  # ATR is on, passed on 2nd attempt
        assert pass_spans[0].get_tag("test.status") == "pass"
        assert fail_spans[0].get_tag("test.status") == "fail"
        assert flaky_spans[0].get_tag("test.status") == "fail"
        assert flaky_spans[1].get_tag("test.status") == "pass"
        assert rec.ret == 1
