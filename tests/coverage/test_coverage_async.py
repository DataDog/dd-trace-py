import pytest


@pytest.mark.subprocess
def test_coverage_async_function():
    """
    Async functions in Python 3.10 have an initial GEN_START instruction with no corresponding line number.
    This test ensures we can handle this case correctly.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.async_code import call_async_function

    ModuleCodeCollector.start_coverage()
    call_async_function()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    expected_executable = {
        "tests/coverage/included_path/async_code.py": {1, 3, 4, 6, 7},
    }
    expected_covered = {
        "tests/coverage/included_path/async_code.py": {4, 7},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/async_code.py": {1, 3, 4, 6, 7},
    }

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"
