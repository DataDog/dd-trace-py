import json
import os

import mock
import pytest

import ddtrace
from ddtrace.contrib.pytest.plugin import is_enabled
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.recorder import _CIVisibilitySettings
from tests.ci_visibility.util import _patch_dummy_writer
from tests.utils import TracerTestCase
from tests.utils import override_env


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
        assert COVERAGE_TAG_NAME in first_suite_span.get_tags()
        tag_data = json.loads(first_suite_span.get_tag(COVERAGE_TAG_NAME))
        files = sorted(tag_data["files"], key=lambda x: x["filename"])

        assert len(files) == 3

        assert files[2]["filename"] == "test_cov.py"
        assert len(files[2]["segments"]) == 5
        assert files[2]["segments"][0] == [5, 0, 5, 0, -1]
        assert files[2]["segments"][1] == [8, 0, 9, 0, -1]
        assert files[2]["segments"][2] == [12, 0, 13, 0, -1]
        assert files[2]["segments"][3] == [21, 0, 22, 0, -1]
        assert files[2]["segments"][4] == [35, 0, 36, 0, -1]

        assert files[0]["filename"] == "lib_fn.py"
        assert len(files[0]["segments"]) == 1
        assert files[0]["segments"][0] == [2, 0, 2, 0, -1]

        assert files[1]["filename"] == "ret_false.py"
        assert len(files[1]["segments"]) == 1
        assert files[1]["segments"][0] == [1, 0, 2, 0, -1]

        second_suite_span = test_suite_spans[-1]
        assert second_suite_span.get_tag("type") == "test_suite_end"
        assert COVERAGE_TAG_NAME in second_suite_span.get_tags()
        tag_data = json.loads(second_suite_span.get_tag(COVERAGE_TAG_NAME))
        files = sorted(tag_data["files"], key=lambda x: x["filename"])
        assert len(files) == 2
        assert files[1]["filename"] == "test_cov_second.py"
        assert files[0]["filename"] == "ret_false.py"
        assert len(files[1]["segments"]) == 1
        assert files[1]["segments"][0] == [4, 0, 5, 0, -1]
        assert len(files[0]["segments"]) == 1
        assert files[0]["segments"][0] == [2, 0, 2, 0, -1]

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
        assert COVERAGE_TAG_NAME in first_suite_span.get_tags()
        tag_data = json.loads(first_suite_span.get_tag(COVERAGE_TAG_NAME))
        files = sorted(tag_data["files"], key=lambda x: x["filename"])

        assert len(files) == 3

        assert files[2]["filename"] == "test_cov.py"
        assert len(files[2]["segments"]) == 2
        assert files[2]["segments"][0] == [5, 0, 5, 0, -1]
        assert files[2]["segments"][1] == [8, 0, 9, 0, -1]

        assert files[0]["filename"] == "lib_fn.py"
        assert len(files[0]["segments"]) == 1
        assert files[0]["segments"][0] == [2, 0, 2, 0, -1]

        assert files[1]["filename"] == "ret_false.py"
        assert len(files[1]["segments"]) == 1
        assert files[1]["segments"][0] == [1, 0, 2, 0, -1]

        second_suite_span = test_suite_spans[1]
        assert second_suite_span.get_tag("type") == "test_suite_end"
        assert COVERAGE_TAG_NAME not in second_suite_span.get_tags()
