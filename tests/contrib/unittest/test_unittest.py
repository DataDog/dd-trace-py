import os
import sys

import pytest

from tests.utils import TracerTestCase


class UnittestTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    def test_unittest_pass(self):
        """Test with a `unittest` test which should pass."""
        import unittest
        from ddtrace import patch

        patch(unittest=True)

        class UnittestExampleTestCase(unittest.TestCase):
            def test_will_pass(self):
                self.assertTrue(2 == 2)

        suite = unittest.TestLoader().loadTestsFromTestCase(UnittestExampleTestCase)
        unittest.TextTestRunner(verbosity=0).run(suite)

        print(self)