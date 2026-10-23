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

    def test_subprocess_assertions_survive_optimization(self):
        py_file = self.testdir.makepyfile(
            """
            import pytest
            @pytest.mark.subprocess(env={"PYTHONOPTIMIZE": "1"})
            def test_fail():
                assert False, "child assertion"
            """
        )
        self.inline_run(os.path.basename(py_file.strpath)).assertoutcome(failed=1)

    def test_process_fixture_is_unskippable(self):
        py_file = self.testdir.makepyfile(
            """
            def test_selection(request, python_process):
                reasons = [m.kwargs.get("reason") for m in request.node.iter_markers("skipif")]
                assert "datadog_itr_unskippable" in reasons
            """
        )
        self.inline_run(os.path.basename(py_file.strpath)).assertoutcome(passed=1)

    def test_child_skip_is_reported(self):
        py_file = self.testdir.makepyfile(
            """
            import pytest
            @pytest.mark.subprocess()
            def test_skip():
                import unittest
                raise unittest.SkipTest("child platform")
            """
        )
        self.inline_run(os.path.basename(py_file.strpath)).assertoutcome(skipped=1)

    def test_command_scenario_preserves_unnamed_entrypoint(self):
        py_file = self.testdir.makepyfile(
            """
            import pytest
            @pytest.mark.subprocess(command=True)
            def test_command():
                import sys
                from ddtrace.internal.settings._inferred_base_service import detect_service
                assert sys.argv[0] == '-c'
                assert detect_service(sys.argv) is None
            """
        )
        self.inline_run(os.path.basename(py_file.strpath)).assertoutcome(passed=1)

    def test_subprocess_imported_assertions_survive_optimization(self):
        helper = self.testdir.makepyfile(
            process_assertion_helper="""
            import unittest
            class Case(unittest.TestCase):
                def runTest(self):
                    assert False, "imported child assertion"
            """
        )
        py_file = self.testdir.makepyfile(
            f"""
            import pytest
            @pytest.mark.subprocess(env={{"PYTHONOPTIMIZE": "1"}}, test_assertions=True)
            def test_fail():
                import sys
                import tests
                tests.__path__.append({str(helper.dirpath())!r})
                from tests.process import case_context
                from tests.process_assertion_helper import Case
                assert sys.flags.optimize == 1
                with case_context(Case) as case:
                    case.runTest()
            """
        )
        rec = self.inline_run(os.path.basename(py_file.strpath))
        rec.assertoutcome(failed=1)
        report = next(report for report in rec.getreports("pytest_runtest_logreport") if report.when == "call")
        assert "AssertionError: imported child assertion" in str(report.longrepr)
