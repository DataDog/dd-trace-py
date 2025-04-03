import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring coverage is only used in Python 3.12+")
@pytest.mark.subprocess
def test_code_coverage_tool_clash():
    """If another tool is already registered as the `sys.monitoring` coverage tool, do not collect coverage."""
    import os
    from pathlib import Path
    import sys

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "something_else")

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
        "tests/coverage/included_path/async_code.py": set(),
    }
    expected_covered = {}
    expected_covered_with_imports = {}

    assert (
        executable == expected_executable
    ), f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert (
        covered_with_imports == expected_covered_with_imports
    ), f"Covered lines with imports mismatch: expected={expected_covered_with_imports} vs actual={covered_with_imports}"
    assert line_number == 7
