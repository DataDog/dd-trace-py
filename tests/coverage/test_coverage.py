"""This file includes various tests that exercise the internal coverage collection module

Tests use the subprocess pytest mark to ensure that coverage collection happens in a clean environment.

Tests that cover import-time dependencies are meant to catch issues (important to the Intelligent Test Runner) with
lines that code technically depends on (eg: imported functions, classes, or constants), but are executed at import
time rather than at code execution time.
"""

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


# @pytest.mark.subprocess


def test_coverage_import_time_lib():
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

        from tests.coverage.included_path.import_time_callee import called_in_session_import_time

        ModuleCodeCollector.start_coverage()
        called_in_session_import_time()
        ModuleCodeCollector.stop_coverage()

        executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
        covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
        covered_with_imports = _get_relpath_dict(
            cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
        )

    expected_executable = {
        "tests/coverage/included_path/import_time_callee.py": {1, 2, 4, 7, 8, 10, 13, 15},
        "tests/coverage/included_path/import_time_lib.py": {1, 3, 6, 7, 8},
        "tests/coverage/included_path/nested_import_time_lib.py": {1, 4, 5, 6},
    }
    expected_covered = {
        "tests/coverage/included_path/import_time_callee.py": {2, 4},
        "tests/coverage/included_path/import_time_lib.py": {1, 3, 6, 7, 8},
        "tests/coverage/included_path/nested_import_time_lib.py": {1, 4},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/import_time_callee.py": {1, 2, 4, 7, 13},
        "tests/coverage/included_path/import_time_lib.py": {1, 3, 6, 7, 8},
        "tests/coverage/included_path/nested_import_time_lib.py": {1, 4},
    }

    assert (
        executable.keys() == expected_executable.keys()
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert (
        covered.keys() == expected_covered.keys()
    ), f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports.keys() == expected_covered_with_imports.keys()
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"


# @pytest.mark.subprocess


def test_coverage_import_time_function():
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

        # The following constant is imported, but not used, so that, by the time it is also imported in
        # calls_function_imported_in_function , it will be only be covered if the include_imported flag
        # is set to True
        from tests.coverage.included_path.imported_in_function_lib import module_level_constant  # noqa

        from tests.coverage.included_path.import_time_callee import calls_function_imported_in_function

        ModuleCodeCollector.start_coverage()
        calls_function_imported_in_function()
        ModuleCodeCollector.stop_coverage()

        lines = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
        covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
        covered_with_imports = _get_relpath_dict(
            cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
        )

    expected_lines = {
        "tests/coverage/included_path/imported_in_function_lib.py": {1, 2, 3, 4, 7},
        "tests/coverage/included_path/import_time_callee.py": {1, 2, 4, 7, 8, 10, 13, 15},
    }
    expected_covered = {"tests/coverage/included_path/import_time_callee.py": {8, 10}}
    expected_covered_with_imports = {
        "tests/coverage/included_path/import_time_callee.py": {1, 7, 8, 10, 13},
        "tests/coverage/included_path/imported_in_function_lib.py": {1, 2, 3, 4, 7},
    }

    assert (
        lines.keys() == expected_lines.keys()
    ), f"Executable lines mismatch: expected={expected_lines} vs actual={lines}"
    assert (
        covered.keys() == expected_covered.keys()
    ), f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports.keys() == expected_covered_with_imports.keys()
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"
