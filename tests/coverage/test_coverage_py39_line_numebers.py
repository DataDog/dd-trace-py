import pytest


@pytest.mark.subprocess
def test_coverage_py39_line_numbers():
    """
    In Python 3.9, `dis.findlinestarts()` (used to determine where to insert instrumentation calls) can return
    imprecise line number values in some corner cases. This test ensures that we do not explode in such cases (even
    though the line number information may not be 100% precise).
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.py39_line_numbers import call_all_functions

    ModuleCodeCollector.start_coverage()
    call_all_functions()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    expected_executable = {
        "tests/coverage/included_path/py39_line_numbers.py": {1, 3, 4, 7, 8, 9, 12, 13, 14, 17, 18, 19},
    }
    expected_covered = {
        "tests/coverage/included_path/py39_line_numbers.py": {4, 8, 9, 13, 14, 18, 19},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/py39_line_numbers.py": {1, 3, 4, 7, 8, 9, 12, 13, 14, 17, 18, 19},
    }

    assert executable == expected_executable, (
        f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    )
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert covered_with_imports == expected_covered_with_imports, (
        f"Covered lines with imports mismatch: expected={expected_covered_with_imports} "
        f"vs actual={covered_with_imports}"
    )
