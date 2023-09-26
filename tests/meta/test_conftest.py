import os

import pytest

from tests.utils import TracerTestCase
from tests.utils import override_env


class ConftestTestCase(TracerTestCase):
    """
    Test case to verify conftest code works as expected
    """

    @pytest.fixture(autouse=True)
    def fixtures(self, testdir):
        """
        Fixtures to use in tests
        """
        self.testdir = testdir

    def makeconftest(self, conftest_rel_path="../conftest.py"):
        """
        Copy tests/conftest.py contents to the testdir conftest
        """
        this_dir = os.path.dirname(__file__)
        abs_file_path = os.path.join(this_dir, conftest_rel_path)
        with open(abs_file_path, encoding="utf-8") as conftest:
            self.testdir.makeconftest(conftest.read())

    def inline_run(self, *args):
        """
        Override inline_run to use our conftest
        """
        with override_env({"DD_API_KEY": "foobar.baz"}):
            self.makeconftest()
            return self.testdir.inline_run(*args)

    def test_subprocess_fail(self):
        """
        Test a failing test with subprocess decorator
        """
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.subprocess()
            def test_fail():
                assert False
            """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=0, failed=1, skipped=0)

    def test_subprocess_pass(self):
        """
        Test a passing test with subprocess decorator
        """
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.subprocess()
            def test_pass():
                assert True
            """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=1, failed=0, skipped=0)

    def test_subprocess_skip(self):
        """
        Test a skipping test with subprocess decorator
        """
        py_file = self.testdir.makepyfile(
            """
            import pytest

            @pytest.mark.skip
            @pytest.mark.subprocess()
            def test_pass():
                assert True
            """
        )
        file_name = os.path.basename(py_file.strpath)
        rec = self.inline_run(file_name)
        rec.assertoutcome(passed=0, failed=0, skipped=1)
