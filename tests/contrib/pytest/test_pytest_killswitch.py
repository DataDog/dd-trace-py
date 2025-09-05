import os
from unittest import mock

import pytest

from tests.contrib.pytest.test_pytest import PytestTestCaseBase


class TestPytestKillswitch(PytestTestCaseBase):
    """Test that the pytest plugin killswitch prevents ddtrace from being imported."""

    def test_ddtrace_not_imported_when_killswitch_present(self):
        """Test that ddtrace is not imported when DD_CIVISIBILITY_ENABLED=0/false."""
        py_file = self.testdir.makepyfile(
            """
            import sys
            def test_ddtrace_not_imported():
                ddtrace_modules = [m for m in sys.modules.keys() if m.startswith('ddtrace')]
                assert len(ddtrace_modules) == 0, f"ddtrace modules found: {len(ddtrace_modules)}"

            def test_pass():
                assert True
            """
        )
        file_name = os.path.basename(py_file.strpath)
        
        # Test all combinations of killswitch values and CLI args
        for value in ["0", "false"]:
            for arg in [None, "--ddtrace", "--no-ddtrace"]:
                with self.subTest(value=value, arg=arg):
                    if arg:
                        result = self.subprocess_run(arg, file_name, env={"DD_CIVISIBILITY_ENABLED": value})
                    else:
                        result = self.subprocess_run(file_name, env={"DD_CIVISIBILITY_ENABLED": value})
                    result.assert_outcomes(passed=2)

    def test_ddtrace_imported_when_killswitch_inactive(self):
        """Test that ddtrace is imported when DD_CIVISIBILITY_ENABLED is truthy or unset."""
        py_file = self.testdir.makepyfile(
            """
            import sys
            def test_ddtrace_imported():
                ddtrace_modules = [m for m in sys.modules.keys() if m.startswith('ddtrace')]
                assert len(ddtrace_modules) > 0, f"Expected ddtrace modules, found {len(ddtrace_modules)}"

            def test_pass():
                assert True
            """
        )
        file_name = os.path.basename(py_file.strpath)

        # Test combinations of enabled values and CLI args
        for value in ["1", "true", None]:
            for arg in ["--ddtrace", "--no-ddtrace"]:
                with self.subTest(value=value, arg=arg):
                    if value:
                        result = self.subprocess_run(arg, file_name, env={"DD_CIVISIBILITY_ENABLED": value})
                    else:
                        result = self.subprocess_run(arg, file_name)
                    result.assert_outcomes(passed=2)

    def test_asbool_function_handles_killswitch_values(self):
        """Test that the asbool function correctly handles killswitch values."""
        from _ddtrace.internal.utils.formats import asbool

        # Values that should disable (False)
        for value in ["0", "false", "False", "FALSE", None, False]:
            assert asbool(value) is False, f"asbool({value!r}) should be False"

        # Values that should enable (True)
        for value in ["1", "true", "True", "TRUE", True]:
            assert asbool(value) is True, f"asbool({value!r}) should be True"
