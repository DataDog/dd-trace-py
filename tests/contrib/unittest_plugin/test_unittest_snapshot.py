import subprocess

import pytest

from tests.utils import TracerTestCase
from tests.utils import override_env
from tests.utils import snapshot


SNAPSHOT_IGNORES = [
    "meta.library_version",
    "meta.os.architecture",
    "meta.os.platform",
    "meta.os.version",
    "meta.runtime-id",
    "meta.runtime.version",
    "meta.test.framework_version",
    "meta.test_module_id",
    "meta.test_session_id",
    "meta.test_suite_id",
    "metrics._dd.top_level",
    "metrics._dd.tracer_kr",
    "metrics._sampling_priority_v1",
    "metrics.process_id",
    "duration",
    "start",
]


class UnittestSnapshotTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    @snapshot(ignores=SNAPSHOT_IGNORES)
    def test_unittest_generates_source_file_data(self):
        ret_false = """
        def ret_false():
            return False
        """
        self.testdir.makepyfile(ret_false=ret_false)
        lib_fn = """
        def lib_fn():
            return True
        """
        self.testdir.makepyfile(lib_fn=lib_fn)
        test_source_file = """
        import unittest

        class CoverageTestCase(unittest.TestCase):
            def test_first(self):
                from lib_fn import lib_fn
                assert lib_fn()
            def test_second(self):
                from ret_false import ret_false
                assert not ret_false()
            def test_third(self):
                from ret_false import ret_false
                assert ret_false()
        """
        self.testdir.makepyfile(test_source_file=test_source_file)
        self.testdir.chdir()
        with override_env(dict(DD_API_KEY="foobar.baz")):
            subprocess.run(
                ["ddtrace-run", "python", "-m", "unittest"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
