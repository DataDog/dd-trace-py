import pytest


@pytest.mark.subprocess
def test_coverage_async_function():
    """
    Async functions in Python 3.10 have an initial GEN_START instruction with no corresponding line number.
    This test ensures we can handle this case correctly and build the line number table correctly in the instrumented
    functions.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

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
        "tests/coverage/included_path/async_code.py": {1, 2, 5, 6, 7, 8, 9, 10, 13, 14},
    }
    expected_covered = {
        "tests/coverage/included_path/async_code.py": {6, 7, 8, 9, 10, 14},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/async_code.py": {1, 2, 5, 6, 7, 8, 9, 10, 13, 14},
    }

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"
    assert line_number == 7
