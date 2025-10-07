import sys

import pytest


@pytest.fixture(autouse=True)
def cleanup_coverage_modules():
    """Clean up coverage collector and imported test modules between tests."""
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    # Store modules to remove after test
    modules_to_remove = [key for key in sys.modules.keys() if key.startswith("tests.coverage.included_path")]

    yield

    # Clean up after test
    if ModuleCodeCollector._instance:
        ModuleCodeCollector._instance.uninstall()
        ModuleCodeCollector._instance = None

    # Remove imported test modules so they can be re-imported fresh in the next test
    for module_name in modules_to_remove:
        sys.modules.pop(module_name, None)

    # Also remove any modules that were imported during the test
    for key in list(sys.modules.keys()):
        if key.startswith("tests.coverage.included_path"):
            sys.modules.pop(key, None)


def test_coverage_async_function():
    """
    Async functions in Python 3.10 have an initial GEN_START instruction with no corresponding line number.
    This test ensures we can handle this case correctly and build the line number table correctly in the instrumented
    functions.
    """
    import os
    from pathlib import Path
    from unittest.mock import patch

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    # Mock is_user_code to return True for test modules
    # (in some environments, test code may be classified as third-party)
    with patch("ddtrace.internal.coverage.code.is_user_code", return_value=True):
        install(include_paths=[include_path], collect_import_time_coverage=True)

        from tests.coverage.included_path.async_code import call_async_function_and_report_line_number

        ModuleCodeCollector.start_coverage()
        line_number = call_async_function_and_report_line_number()
        ModuleCodeCollector.stop_coverage()

        executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
        covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
        covered_with_imports = _get_relpath_dict(
            cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
        )

        expected_executable = {
            "tests/coverage/included_path/async_code.py": None,  # Irrelevant lines, comparing keys only
        }
        expected_covered = {
            "tests/coverage/included_path/async_code.py": None,  # Irrelevant lines, comparing keys only
        }
        expected_covered_with_imports = {
            "tests/coverage/included_path/async_code.py": None,  # Irrelevant lines, comparing keys only
        }

        assert (
            executable.keys() == expected_executable.keys()
        ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
        assert (
            covered.keys() == expected_covered.keys()
        ), f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
        assert (
            covered_with_imports.keys() == expected_covered_with_imports.keys()
        ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports}"
        " vs actual={covered_with_imports}"
        assert line_number == 7
