import os
from unittest import mock

import pytest

from ddtrace.contrib.internal.pytest._utils import _pytest_version_supports_itr
from ddtrace.ext.test_visibility import ITR_SKIPPING_LEVEL
from ddtrace.internal.ci_visibility._api_client import ITRData
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.coverage.util import collapse_ranges
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from tests.ci_visibility.api_client._util import _make_fqdn_suite_ids
from tests.ci_visibility.util import _ci_override_env
from tests.ci_visibility.util import _mock_ddconfig_test_visibility
from tests.contrib.pytest.test_pytest import PytestTestCaseBase
from tests.contrib.pytest.test_pytest import _fetch_test_to_skip_side_effect


pytestmark = pytest.mark.skipif(not _pytest_version_supports_itr(), reason="pytest version does not support coverage")


def _get_tuples_from_bytearray(bitmap):
    coverage_lines = CoverageLines()
    coverage_lines._lines = bitmap
    return collapse_ranges(coverage_lines.to_sorted_list())


def _get_tuples_from_segments(segments):
    return list((segment[0], segment[2]) for segment in segments)


def _get_span_coverage_data(span):
    """Returns an abstracted view of the coverage data from the span that is independent of the coverage format."""
    tag_data = span.get_struct_tag(COVERAGE_TAG_NAME)
    assert tag_data is not None, f"Coverage data not found in span {span}"
    return {file_data["filename"]: _get_tuples_from_bytearray(file_data["bitmap"]) for file_data in tag_data["files"]}


class PytestTestCase(PytestTestCaseBase):
    def test_pytest_will_report_coverage_by_suite_with_pytest_skipped(self):
        self.testdir.makepyfile(
            ret_false="""
        def ret_false():
            return False
        """
        )
        self.testdir.makepyfile(
            lib_fn="""
        def lib_fn():
            return True
        """
        )
        py_cov_file = self.testdir.makepyfile(
            test_cov="""
        import pytest
        from lib_fn import lib_fn

        def test_cov():
            assert lib_fn()

        def test_second():
            from ret_false import ret_false
            assert not ret_false()

        def test_pytest_skip():
            two = 1 + 1
            pytest.skip()
            assert True == False

        @pytest.mark.skip
        def test_mark_skip():
            assert True == False

        def test_make_sure_we_dont_just_accidentally_win():
            assert True
            assert not False

        @pytest.mark.skipif(True is True, reason="True is True")
        def test_mark_skipif():
            assert True == False

        def skipif_false_check():
            return False
        skipif_false_decorator = pytest.mark.skipif(
            skipif_false_check(), reason="skip if False"
        )
        @skipif_false_decorator
        def test_skipif_mark_false():
            two = 1 + 1
            assert True is False
            assert False is True

        def skipif_true_check():
            return True
        skipif_true_decorator = pytest.mark.skipif(
            skipif_true_check(), reason="skip is True"
        )
        @skipif_true_decorator
        def test_skipif_mark_true():
            assert True is False
        """
        )
        py_cov_file2 = self.testdir.makepyfile(
            test_cov_second="""
        import pytest

        def test_second():
            from ret_false import ret_false
            assert not ret_false()
        """
        )

        with _ci_override_env({"DD_API_KEY": "foobar.baz", "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True"}), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(True, False, False, True),
        ), _mock_ddconfig_test_visibility(itr_skipping_level=ITR_SKIPPING_LEVEL.SUITE):
            self.inline_run(
                "-p",
                "no:randomly",
                "--ddtrace",
                os.path.basename(py_cov_file.strpath),
                os.path.basename(py_cov_file2.strpath),
            )
        spans = self.pop_spans()

        test_suite_spans = [span for span in spans if span.get_tag("type") == "test_suite_end"]
        first_suite_span = test_suite_spans[0]
        assert first_suite_span.get_tag("type") == "test_suite_end"
        first_suite_coverage = _get_span_coverage_data(first_suite_span)
        assert len(first_suite_coverage) == 3
        assert first_suite_coverage["/test_cov.py"] == [
            (1, 2),
            (4, 5),
            (7, 9),
            (11, 13),
            (16, 17),
            (20, 22),
            (24, 25),
            (28, 31),
            (33, 36),
            (39, 42),
            (44, 45),
        ]
        assert first_suite_coverage["/lib_fn.py"] == [(1, 2)]
        assert first_suite_coverage["/ret_false.py"] == [(1, 2)]

        second_suite_span = test_suite_spans[-1]
        assert second_suite_span.get_tag("type") == "test_suite_end"
        second_suite_coverage = _get_span_coverage_data(second_suite_span)
        assert len(second_suite_coverage) == 2
        assert second_suite_coverage["/test_cov_second.py"] == [(1, 1), (3, 5)]
        assert second_suite_coverage["/ret_false.py"] == [(1, 2)]

    def test_pytest_will_report_coverage_by_suite_with_itr_skipped(self):
        self.testdir.makepyfile(
            ret_false="""
        def ret_false():
            return False
        """
        )
        self.testdir.makepyfile(
            lib_fn="""
        def lib_fn():
            return True
        """
        )
        py_cov_file = self.testdir.makepyfile(
            test_cov="""
        import pytest
        from lib_fn import lib_fn

        def test_cov():
            assert lib_fn()

        def test_second():
            from ret_false import ret_false
            assert not ret_false()
        """
        )
        py_cov_file2 = self.testdir.makepyfile(
            test_cov_second="""
        import pytest

        def test_second():
            from ret_false import ret_false
            assert not ret_false()
        """
        )

        _itr_data = ITRData(
            skippable_items=_make_fqdn_suite_ids(
                [
                    ("", "test_cov_second.py"),
                ]
            )
        )

        with _ci_override_env(
            {
                "DD_API_KEY": "foobar.baz",
                "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",
                "DD_APPLICATION_KEY": "not_an_app_key_at_all",
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "True",
            },
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=TestVisibilityAPISettings(True, True, False, True),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip",
            side_effect=_fetch_test_to_skip_side_effect(_itr_data),
        ), _mock_ddconfig_test_visibility(
            itr_skipping_level=ITR_SKIPPING_LEVEL.SUITE
        ):
            self.inline_run(
                "-p",
                "no:randomly",
                "--ddtrace",
                os.path.basename(py_cov_file.strpath),
                os.path.basename(py_cov_file2.strpath),
            )
        spans = self.pop_spans()

        test_suite_spans = [span for span in spans if span.get_tag("type") == "test_suite_end"]
        first_suite_span = test_suite_spans[0]
        assert first_suite_span.get_tag("type") == "test_suite_end"
        second_suite_span = test_suite_spans[1]
        assert second_suite_span.get_tag("type") == "test_suite_end"

        first_suite_coverage = _get_span_coverage_data(first_suite_span)

        assert len(first_suite_coverage) == 3
        assert first_suite_coverage["/test_cov.py"] == [(1, 2), (4, 5), (7, 9)]
        assert first_suite_coverage["/lib_fn.py"] == [(1, 2)]
        assert first_suite_coverage["/ret_false.py"] == [(1, 2)]
        assert second_suite_span.get_struct_tag(COVERAGE_TAG_NAME) is None
