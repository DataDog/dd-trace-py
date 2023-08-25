import unittest
import pytest

from ddtrace.contrib.unittest.patch import _set_tracer
from ddtrace.contrib.unittest.patch import patch
from tests.utils import TracerTestCase
from tests.utils import override_env


class UnittestTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    with override_env(dict(DD_API_KEY="foobar.baz")):
        patch()

        @pytest.mark.snapshot
        def test_unittest_run(self):
            _set_tracer(self.tracer)

            class MyTestSuite(unittest.TestCase):
                def test_will_pass(self):
                    self.assertTrue(1 == 1)

                def test_will_fail(self):
                    self.assertTrue(1 == 2)

                @unittest.skip(reason="we will need to skip this!")
                def test_will_be_skipped(self):
                    self.assertTrue(1 == 1)

            suite = unittest.TestLoader().loadTestsFromTestCase(MyTestSuite)
            unittest.TextTestRunner(verbosity=0).run(suite)
