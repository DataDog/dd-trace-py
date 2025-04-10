"""Tests Early Flake Detection (EFD) functionality

The tests in this module only validate the behavior of EFD, so only counts and statuses of tests, retries, and sessions
are checked.

- The same known tests are used to override fetching of known tests.
- The session object is patched to never be a faulty session, by default.
"""
from unittest import mock
from xml.etree import ElementTree

import pytest

from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_efd
from ddtrace.internal.ci_visibility._api_client import EarlyFlakeDetectionSettings
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from tests.ci_visibility.api_client._util import _make_fqdn_test_ids
from tests.ci_visibility.util import _fetch_known_tests_side_effect
from tests.ci_visibility.util import _get_default_civisibility_ddconfig
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _get_spans_from_list
from tests.utils import override_env


pytestmark = pytest.mark.skipif(
    not _pytest_version_supports_efd(), reason="Early Flake Detection requires pytest >=7.0"
)

_KNOWN_TEST_IDS = _make_fqdn_test_ids(
    [
        ("", "test_known_pass.py", "test_known_passes_01"),
        ("", "test_known_pass.py", "test_known_passes_02"),
        ("", "test_known_fail.py", "test_known_fails_01"),
        ("", "test_known_fail.py", "test_known_fails_02"),
    ]
)

_TEST_KNOWN_PASS_CONTENT = """
def test_known_passes_01():
    assert True
def test_known_passes_02():
    assert True
"""

_TEST_KNOWN_FAIL_CONTENT = """
def test_known_fails_01():
    assert False

def test_known_fails_02():
    assert False
"""

_TEST_NEW_PASS_CONTENT = """
def test_new_passes_01():
    assert True
"""

_TEST_NEW_FAIL_CONTENT = """
def test_new_fails_01():
    assert False
"""

_TEST_NEW_FLAKY_CONTENT = """
last_flake = False
def test_new_flaky_01():
    global last_flake
    last_flake = not last_flake
    assert last_flake
"""

_TEST_NEW_SKIP_CONTENT = """
import pytest
@pytest.mark.skip
def test_new_skips_01():
    assert False

def test_new_skips_02():
    pytest.skip()
"""

_TEST_NEW_FAILS_SETUP = """
import pytest

@pytest.fixture
def fails_setup():
    assert False

def test_fails_setup_01(fails_setup):
    assert True
"""

_TEST_NEW_FAILS_TEARDOWN = """
import pytest

@pytest.fixture
def fails_teardown():
    yield
    assert False

def test_fails_teardown_01(fails_teardown):
    assert True
"""


class PytestEFDTestCase(PytestTestCaseBase):
    @pytest.fixture(autouse=True, scope="function")
    def set_up_efd(self):
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_known_tests",
            return_value=_KNOWN_TEST_IDS,
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                early_flake_detection=EarlyFlakeDetectionSettings(enabled=True, faulty_session_threshold=90),
                known_tests_enabled=True,
            ),
        ):
            yield

    def test_pytest_efd_no_ddtrace_does_not_retry(self):
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_known_fail=_TEST_KNOWN_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_fail=_TEST_NEW_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)
        rec = self.inline_run("-q")
        rec.assertoutcome(passed=4, failed=3)
        assert len(self.pop_spans()) == 0

    def test_pytest_efd_no_new_tests_does_not_retry(self):
        """Tests that no retries will happen if the session is faulty because all tests appear new"""
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_known_fail=_TEST_KNOWN_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_fail=_TEST_NEW_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_known_tests",
            side_effect=_fetch_known_tests_side_effect(set()),
        ):
            rec = self.inline_run("--ddtrace")
            rec.assertoutcome(passed=4, failed=3)
            assert len(self.pop_spans()) == 14

    def test_pytest_efd_env_var_disables_retrying(self):
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_known_fail=_TEST_KNOWN_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_fail=_TEST_NEW_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)
        with override_env({"DD_CIVISIBILITY_EARLY_FLAKE_DETECTION_ENABLED": "0"}), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
        ):
            rec = self.inline_run("--ddtrace")
            rec.assertoutcome(passed=4, failed=3)

    def test_pytest_efd_env_var_does_not_override_api(self):
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_known_fail=_TEST_KNOWN_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_fail=_TEST_NEW_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)
        with override_env({"DD_CIVISIBILITY_EARLY_FLAKE_DETECTION_ENABLED": "1"}), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(early_flake_detection=EarlyFlakeDetectionSettings(enabled=False)),
        ):
            rec = self.inline_run("--ddtrace")
            rec.assertoutcome(passed=4, failed=3)

    def test_pytest_efd_disabled_when_kte_disabled(self):
        """Tests that EFD is disabled when Known Tests Enabled (KTE) is disabled, even if EFD is enabled"""
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                early_flake_detection=EarlyFlakeDetectionSettings(enabled=True, faulty_session_threshold=90),
                known_tests_enabled=False,
            ),
        ):
            rec = self.inline_run("--ddtrace")
            rec.assertoutcome(passed=4)
            spans = self.pop_spans()

            # Verify no retries happened since EFD should be disabled when KTE is disabled
            new_flaky_spans = _get_spans_from_list(spans, "test", "test_new_flaky_01")
            assert len(new_flaky_spans) == 1
            assert new_flaky_spans[0].get_tag("test.is_retry") != "true"

            # Verify that test.is_new is NOT set when KTE is disabled, even for new tests
            assert new_flaky_spans[0].get_tag("test.is_new") != "true"

            new_passes_spans = _get_spans_from_list(spans, "test", "test_new_passes_01")
            assert len(new_passes_spans) == 1
            assert new_passes_spans[0].get_tag("test.is_retry") != "true"

            # Verify that test.is_new is NOT set for new passing tests either when KTE is disabled
            assert new_passes_spans[0].get_tag("test.is_new") != "true"

    def test_pytest_efd_disabled_when_kte_enabled_but_efd_disabled(self):
        """Tests that EFD is disabled when KTE is enabled but EFD itself is disabled"""
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                early_flake_detection=EarlyFlakeDetectionSettings(enabled=False, faulty_session_threshold=90),
                known_tests_enabled=True,
            ),
        ):
            rec = self.inline_run("--ddtrace")
            rec.assertoutcome(passed=4)
            spans = self.pop_spans()

            # Verify no retries happened since EFD is disabled even though KTE is enabled
            new_flaky_spans = _get_spans_from_list(spans, "test", "test_new_flaky_01")
            assert len(new_flaky_spans) == 1
            assert new_flaky_spans[0].get_tag("test.is_retry") != "true"

            # Verify that test.is_new is still being set even when EFD is disabled but KTE is enabled
            assert new_flaky_spans[0].get_tag("test.is_new") == "true"

            new_passes_spans = _get_spans_from_list(spans, "test", "test_new_passes_01")
            assert len(new_passes_spans) == 1
            assert new_passes_spans[0].get_tag("test.is_retry") != "true"

            # Verify that test.is_new is still being set for new passing tests too
            assert new_passes_spans[0].get_tag("test.is_new") == "true"

            # Verify that known tests are not tagged as new
            known_passes_spans = _get_spans_from_list(spans, "test", "test_known_passes_01")
            assert len(known_passes_spans) == 1
            assert known_passes_spans[0].get_tag("test.is_new") != "true"

    def test_pytest_efd_enabled_when_both_kte_and_efd_enabled(self):
        """Tests that EFD is enabled only when both KTE and EFD are enabled"""
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)
        # This test explicitly sets both KTE and EFD to enabled
        with mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                early_flake_detection=EarlyFlakeDetectionSettings(enabled=True, faulty_session_threshold=90),
                known_tests_enabled=True,
            ),
        ):
            self.inline_run("--ddtrace")
            # Instead of checking the outcome, which might include custom EFD statuses,
            # directly check the spans to verify retries happened
            spans = self.pop_spans()

            # Verify retries happened since both KTE and EFD are enabled
            new_flaky_spans = _get_spans_from_list(spans, "test", "test_new_flaky_01")
            assert len(new_flaky_spans) == 11  # 1 original + 10 retries

            flaky_retries = 0
            for span in new_flaky_spans:
                if span.get_tag("test.is_retry") == "true":
                    flaky_retries += 1
            assert flaky_retries == 10

            new_passes_spans = _get_spans_from_list(spans, "test", "test_new_passes_01")
            assert len(new_passes_spans) == 11  # 1 original + 10 retries

            passes_retries = 0
            for span in new_passes_spans:
                if span.get_tag("test.is_retry") == "true":
                    passes_retries += 1
            assert passes_retries == 10

    def test_pytest_efd_env_var_disables_retrying_even_with_kte(self):
        """Tests that EFD env var can disable retries even when both KTE and EFD API settings are enabled"""
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)
        with override_env({"DD_CIVISIBILITY_EARLY_FLAKE_DETECTION_ENABLED": "0"}), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(
                early_flake_detection=EarlyFlakeDetectionSettings(enabled=True, faulty_session_threshold=90),
                known_tests_enabled=True,
            ),
        ):
            rec = self.inline_run("--ddtrace")
            rec.assertoutcome(passed=4)
            spans = self.pop_spans()

            # Verify no retries happened since EFD env var is disabled even though both API settings are enabled
            new_flaky_spans = _get_spans_from_list(spans, "test", "test_new_flaky_01")
            assert len(new_flaky_spans) == 1
            assert new_flaky_spans[0].get_tag("test.is_retry") != "true"

            # Verify that test.is_new is still set because KTE is enabled, even though EFD is disabled by env var
            assert new_flaky_spans[0].get_tag("test.is_new") == "true"

            new_passes_spans = _get_spans_from_list(spans, "test", "test_new_passes_01")
            assert len(new_passes_spans) == 1
            assert new_passes_spans[0].get_tag("test.is_retry") != "true"

            # Verify that test.is_new is set for new passing tests too
            assert new_passes_spans[0].get_tag("test.is_new") == "true"

            # Verify that known tests are not tagged as new
            known_passes_spans = _get_spans_from_list(spans, "test", "test_known_passes_01")
            assert len(known_passes_spans) == 1
            assert known_passes_spans[0].get_tag("test.is_new") != "true"

    def test_pytest_efd_spans(self):
        """Tests that an EFD session properly does the correct number of retries and sets the correct tags"""
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_known_fail=_TEST_KNOWN_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_fail=_TEST_NEW_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_skip=_TEST_NEW_SKIP_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)
        rec = self.inline_run("--ddtrace", "-v")
        assert rec.ret == 1
        spans = self.pop_spans()
        session_span = _get_spans_from_list(spans, "session")[0]
        assert session_span.get_tag("test.status") == "fail"

        module_span = _get_spans_from_list(spans, "module", "")[0]
        assert module_span.get_tag("test.status") == "fail"

        suite_spans = _get_spans_from_list(spans, "suite")
        assert len(suite_spans) == 6
        for suite_span in suite_spans:
            suite_name = suite_span.get_tag("test.suite")
            if suite_name in ("test_new_fail.py", "test_known_fail.py"):
                assert suite_span.get_tag("test.status") == "fail"
            elif suite_name == "test_new_skip.py":
                assert suite_span.get_tag("test.status") == "skip"
            else:
                assert suite_span.get_tag("test.status") == "pass"

        new_fail_spans = _get_spans_from_list(spans, "test", "test_new_fails_01")
        assert len(new_fail_spans) == 11
        new_fail_retries = 0
        for new_fail_span in new_fail_spans:
            assert new_fail_span.get_tag("test.is_new") == "true"
            if new_fail_span.get_tag("test.is_retry") == "true":
                new_fail_retries += 1
        assert new_fail_retries == 10

        new_flaky_spans = _get_spans_from_list(spans, "test", "test_new_flaky_01")
        assert len(new_flaky_spans) == 11
        new_flaky_retries = 0
        for new_flaky_span in new_flaky_spans:
            assert new_flaky_span.get_tag("test.is_new") == "true"
            if new_flaky_span.get_tag("test.is_retry") == "true":
                new_flaky_retries += 1
        assert new_flaky_retries == 10

        new_passes_spans = _get_spans_from_list(spans, "test", "test_new_passes_01")
        assert len(new_passes_spans) == 11
        new_passes_retries = 0
        for new_passes_span in new_passes_spans:
            assert new_passes_span.get_tag("test.is_new") == "true"
            if new_passes_span.get_tag("test.is_retry") == "true":
                new_passes_retries += 1
        assert new_passes_retries == 10

        # Skips are tested twice: once with a skip mark (skips during setup) and once using pytest.skip() in the
        # test body (skips during call)
        new_skips_01_spans = _get_spans_from_list(spans, "test", "test_new_skips_01")
        assert len(new_skips_01_spans) == 11
        new_skips_01_retries = 0
        for new_skips_span in new_skips_01_spans:
            assert new_skips_span.get_tag("test.is_new") == "true"
            if new_skips_span.get_tag("test.is_retry") == "true":
                new_skips_01_retries += 1
        assert new_skips_01_retries == 10
        new_skips_02_spans = _get_spans_from_list(spans, "test", "test_new_skips_02")
        assert len(new_skips_02_spans) == 11
        new_skips_02_retries = 0
        for new_skips_span in new_skips_02_spans:
            assert new_skips_span.get_tag("test.is_new") == "true"
            if new_skips_span.get_tag("test.is_retry") == "true":
                new_skips_02_retries += 1
        assert new_skips_02_retries == 10

        assert len(spans) == 67

    def test_pytest_efd_fails_session_when_test_fails(self):
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_new_fail=_TEST_NEW_FAIL_CONTENT)
        rec = self.inline_run("--ddtrace")
        spans = self.pop_spans()
        assert rec.ret == 1
        assert len(spans) == 17

    def test_pytest_efd_passes_session_when_test_flakes(self):
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)
        rec = self.inline_run("--ddtrace")
        spans = self.pop_spans()
        assert rec.ret == 0
        assert len(spans) == 29

    def test_pytest_efd_does_not_retry_failed_setup(self):
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_fails_setup=_TEST_NEW_FAILS_SETUP)
        rec = self.inline_run("--ddtrace")
        spans = self.pop_spans()
        fails_setup_spans = _get_spans_from_list(spans, "test", "test_fails_setup_01")
        assert len(fails_setup_spans) == 1
        assert fails_setup_spans[0].get_tag("test.is_new") == "true"
        assert fails_setup_spans[0].get_tag("test.is_retry") != "true"
        assert rec.ret == 1
        assert len(spans) == 7

    def test_pytest_efd_does_not_retry_failed_teardown(self):
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_fails_setup=_TEST_NEW_FAILS_TEARDOWN)
        rec = self.inline_run("--ddtrace", "-s")
        spans = self.pop_spans()
        fails_teardown_spans = _get_spans_from_list(spans, "test", "test_fails_teardown_01")
        assert len(fails_teardown_spans) == 1
        assert fails_teardown_spans[0].get_tag("test.is_new") == "true"
        assert fails_teardown_spans[0].get_tag("test.is_retry") != "true"
        assert rec.ret == 1
        assert len(spans) == 7

    def test_pytest_efd_junit_xml(self):
        self.testdir.makepyfile(test_known_pass=_TEST_KNOWN_PASS_CONTENT)
        self.testdir.makepyfile(test_known_fail=_TEST_KNOWN_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_pass=_TEST_NEW_PASS_CONTENT)
        self.testdir.makepyfile(test_new_fail=_TEST_NEW_FAIL_CONTENT)
        self.testdir.makepyfile(test_new_flaky=_TEST_NEW_FLAKY_CONTENT)

        rec = self.inline_run("--ddtrace", "--junit-xml=out.xml")
        assert rec.ret == 1

        test_suite = ElementTree.parse(f"{self.testdir}/out.xml").find("testsuite")
        assert test_suite.attrib["tests"] == "7"
        assert test_suite.attrib["failures"] == "3"
