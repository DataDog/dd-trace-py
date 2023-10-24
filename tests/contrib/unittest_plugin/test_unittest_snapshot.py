import subprocess

import pytest

from tests.utils import TracerTestCase
from tests.utils import override_env


class UnittestSnapshotTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    @pytest.mark.snapshot
    def test_generates_coverage_correctly(self):
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
        self.testdir.makepyfile(
            test_cov="""
        import unittest
        from unittest import mock

        mock.patch(
            "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
            return_value=(True, False))

        class CoverageTestCase(unittest.TestCase):
            def test_cov(self):
                from lib_fn import lib_fn
                assert lib_fn()

            def test_second(self):
                from ret_false import ret_false
                assert not ret_false()
        """
        )
        self.testdir.chdir()
        with override_env(dict(DD_API_KEY="foobar.baz", DD_CIVISIBILITY_ITR_ENABLED="1")):
            result = subprocess.run(
                ["ddtrace-run", "python", "-m", "unittest"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )

            print(result.stdout)
            print(result.stderr)
