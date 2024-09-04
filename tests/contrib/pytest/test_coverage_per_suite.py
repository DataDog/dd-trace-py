import json
import os
from unittest import mock

import pytest

import ddtrace
from ddtrace.contrib.pytest._utils import _USE_PLUGIN_V2
from ddtrace.contrib.pytest.plugin import is_enabled
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.coverage.util import collapse_ranges
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines
from tests.ci_visibility.util import _mock_ddconfig_test_visibility
from tests.ci_visibility.util import _patch_dummy_writer
from tests.utils import TracerTestCase
from tests.utils import override_env


# TODO: investigate why pytest 3.7 does not mark the decorated function line when skipped as covered
_DONT_COVER_SKIPPED_FUNC_LINE = PYTHON_VERSION_INFO <= (3, 8, 0)


def _get_tuples_from_bytearray(bitmap):
    coverage_lines = CoverageLines()
    coverage_lines._lines = bitmap
    return collapse_ranges(coverage_lines.to_sorted_list())


def _get_tuples_from_segments(segments):
    return list((segment[0], segment[2]) for segment in segments)


def _get_span_coverage_data(span, use_plugin_v2=False):
    """Returns an abstracted view of the coverage data from the span that is independent of the coverage format."""
    if use_plugin_v2:
        tag_data = span.get_struct_tag(COVERAGE_TAG_NAME)
        assert tag_data is not None, f"Coverage data not found in span {span}"
        return {
            file_data["filename"]: _get_tuples_from_bytearray(file_data["bitmap"]) for file_data in tag_data["files"]
        }

    else:
        # This will raise an exception and the test will fail if the tag is not found
        tag_data = json.loads(span.get_tag(COVERAGE_TAG_NAME))
        return {
            file_data["filename"]: _get_tuples_from_segments(file_data["segments"]) for file_data in tag_data["files"]
        }


class PytestTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    def inline_run(self, *args):
        """Execute test script with test tracer."""

        class CIVisibilityPlugin:
            @staticmethod
            def pytest_configure(config):
                if is_enabled(config):
                    with _patch_dummy_writer():
                        assert CIVisibility.enabled
                        CIVisibility.disable()
                        CIVisibility.enable(tracer=self.tracer, config=ddtrace.config.pytest)

        return self.testdir.inline_run(*args, plugins=[CIVisibilityPlugin()])

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

        with override_env({"DD_API_KEY": "foobar.baz", "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True"}), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=_CIVisibilitySettings(True, False, False, True),
        ), _mock_ddconfig_test_visibility(itr_skipping_level="suite"):
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
        first_suite_coverage = _get_span_coverage_data(first_suite_span, _USE_PLUGIN_V2)
        assert len(first_suite_coverage) == 3
        if _USE_PLUGIN_V2:
            if _DONT_COVER_SKIPPED_FUNC_LINE:
                assert first_suite_coverage["/test_cov.py"] == [
                    (1, 2),
                    (4, 5),
                    (7, 9),
                    (11, 13),
                    (16, 16),
                    (20, 22),
                    (24, 24),
                    (28, 31),
                    (33, 33),
                    (35, 36),
                    (39, 42),
                    (44, 44),
                ]
            else:
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

        else:
            assert first_suite_coverage["test_cov.py"] == [(5, 5), (8, 9), (12, 13), (21, 22), (35, 36)]
            assert first_suite_coverage["lib_fn.py"] == [(2, 2)]
            assert first_suite_coverage["ret_false.py"] == [(1, 2)]

        second_suite_span = test_suite_spans[-1]
        assert second_suite_span.get_tag("type") == "test_suite_end"
        second_suite_coverage = _get_span_coverage_data(second_suite_span, _USE_PLUGIN_V2)
        assert len(second_suite_coverage) == 2
        if _USE_PLUGIN_V2:
            assert second_suite_coverage["/test_cov_second.py"] == [(1, 1), (3, 5)]
            assert second_suite_coverage["/ret_false.py"] == [(1, 2)]
        else:
            assert second_suite_coverage["test_cov_second.py"] == [(4, 5)]
            assert second_suite_coverage["ret_false.py"] == [(2, 2)]

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

        with override_env(
            {
                "DD_API_KEY": "foobar.baz",
                "_DD_CIVISIBILITY_ITR_SUITE_MODE": "True",
                "DD_APPLICATION_KEY": "not_an_app_key_at_all",
                "DD_CIVISIBILITY_AGENTLESS_ENABLED": "True",
            }
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=_CIVisibilitySettings(True, True, False, True),
        ), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._fetch_tests_to_skip"
        ), mock.patch.object(
            ddtrace.internal.ci_visibility.recorder.CIVisibility,
            "_test_suites_to_skip",
            [
                "test_cov_second.py",
            ],
        ), _mock_ddconfig_test_visibility(
            itr_skipping_level="suite"
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

        first_suite_coverage = _get_span_coverage_data(first_suite_span, _USE_PLUGIN_V2)

        if _USE_PLUGIN_V2:
            assert len(first_suite_coverage) == 3
            assert first_suite_coverage["/test_cov.py"] == [(1, 2), (4, 5), (7, 9)]
            assert first_suite_coverage["/lib_fn.py"] == [(1, 2)]
            assert first_suite_coverage["/ret_false.py"] == [(1, 2)]
            assert second_suite_span.get_struct_tag(COVERAGE_TAG_NAME) is None
        else:
            assert len(first_suite_coverage) == 3
            assert first_suite_coverage["test_cov.py"] == [(5, 5), (8, 9)]
            assert first_suite_coverage["lib_fn.py"] == [(2, 2)]
            assert first_suite_coverage["ret_false.py"] == [(1, 2)]
            assert COVERAGE_TAG_NAME not in second_suite_span.get_tags()
