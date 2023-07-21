import json
import os

import mock
import pytest

import ddtrace
from ddtrace.contrib.pytest.plugin import is_enabled
from ddtrace.internal import compat
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from tests.ci_visibility.test_encoder import _patch_dummy_writer
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

        with override_env(dict(DD_API_KEY="foobar.baz")):
            return self.testdir.inline_run(*args, plugins=[CIVisibilityPlugin()])

    @pytest.mark.skipif(compat.PY2, reason="ddtrace does not support coverage on Python 2")
    def test_pytest_will_report_coverage_by_suite(self):
        self.testdir.makepyfile(
            test_ret_false="""
        def ret_false():
            return False
        """
        )
        self.testdir.makepyfile(
            test_module="""
        def lib_fn():
            return True
        """
        )
        py_cov_file = self.testdir.makepyfile(
            test_cov="""
        import pytest
        from test_module import lib_fn

        def test_cov():
            assert lib_fn()

        def test_second():
            from test_ret_false import ret_false
            assert not ret_false()
        """
        )
        py_cov_file2 = self.testdir.makepyfile(
            test_cov_second="""
        import pytest

        def test_second():
            from test_ret_false import ret_false
            assert not ret_false()
        """
        )

        with mock.patch("ddtrace.contrib.pytest.plugin._get_test_skipping_level", return_value="suite"), mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features", return_value=(True, False)
        ):
            self.inline_run("--ddtrace", os.path.basename(py_cov_file.strpath), os.path.basename(py_cov_file2.strpath))
        spans = self.pop_spans()

        test_suite_spans = [span for span in spans if span.get_tag("type") == "test_suite_end"]
        first_suite_span = test_suite_spans[0]
        assert first_suite_span.get_tag("type") == "test_suite_end"
        assert COVERAGE_TAG_NAME in first_suite_span.get_tags()
        tag_data = json.loads(first_suite_span.get_tag(COVERAGE_TAG_NAME))
        files = sorted(tag_data["files"], key=lambda x: x["filename"])
        assert len(files) == 3
        assert files[0]["filename"] == "test_cov.py"
        assert files[1]["filename"] == "test_module.py"
        assert files[2]["filename"] == "test_ret_false.py"
        assert len(files[0]["segments"]) == 2
        assert files[0]["segments"][0] == [5, 0, 5, 0, -1]
        assert files[0]["segments"][1] == [8, 0, 9, 0, -1]
        assert len(files[1]["segments"]) == 1
        assert files[1]["segments"][0] == [2, 0, 2, 0, -1]
        assert len(files[2]["segments"]) == 1
        assert files[2]["segments"][0] == [1, 0, 2, 0, -1]

        second_suite_span = test_suite_spans[-1]
        assert second_suite_span.get_tag("type") == "test_suite_end"
        assert COVERAGE_TAG_NAME in second_suite_span.get_tags()
        tag_data = json.loads(second_suite_span.get_tag(COVERAGE_TAG_NAME))
        files = sorted(tag_data["files"], key=lambda x: x["filename"])
        assert len(files) == 2
        assert files[0]["filename"] == "test_cov_second.py"
        assert files[1]["filename"] == "test_ret_false.py"
        assert len(files[0]["segments"]) == 1
        assert files[0]["segments"][0] == [4, 0, 5, 0, -1]
        assert len(files[1]["segments"]) == 1
        assert files[1]["segments"][0] == [2, 0, 2, 0, -1]
