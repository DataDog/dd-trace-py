import os
import sys

import pytest

import ddtrace

import subprocess

from ddtrace.contrib.unittest.constants import COMPONENT_VALUE
from ddtrace.contrib.unittest.constants import FRAMEWORK
from ddtrace.contrib.unittest.constants import KIND
from ddtrace.contrib.unittest.constants import TEST_OPERATION_NAME
from ddtrace.ext.test import TEST_TYPE
from ddtrace.internal.ci_visibility import CIVisibility
from tests.ci_visibility.test_encoder import _patch_dummy_writer
from tests.utils import TracerTestCase
from tests.utils import override_env


class UnittestTestCase(TracerTestCase):
    @pytest.fixture(autouse=True)
    def fixtures(self, testdir, monkeypatch, git_repo):
        self.testdir = testdir
        self.monkeypatch = monkeypatch
        self.git_repo = git_repo

    def inline_run(self, *args):
        """Execute test script with test tracer."""

        with override_env(dict(DD_API_KEY="foobar.baz")):
            subprocess.call([sys.executable, '-m', 'unittest', *args])

    def test_unittest_pass(self):
        """Test with benchmark."""
        py_file = self.testdir.makepyfile(
            """
            import unittest
            from ddtrace import patch
            
            patch(unittest=True)
            
            
            class UnittestExampleTestCase(unittest.TestCase):
                def test_will_pass(self):
                    print('..')
                    self.assertTrue(2 != 2)
            
            if __name__ == '__main__':
                print('...')
                unittest.main()
        """
        )
        file_name = os.path.basename(py_file.strpath)
        self.inline_run(file_name)
        spans = self.pop_spans()
        print(spans)
