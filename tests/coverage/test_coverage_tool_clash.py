import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring coverage is only used in Python 3.12+")
@pytest.mark.subprocess
def test_coverage_id_clash_does_not_affect_ddtrace():
    """Another tool holding COVERAGE_ID must not prevent ddtrace from collecting coverage.

    ddtrace's coverage registers with the shared sys.monitoring multiplexer, which owns a single
    tool slot on ddtrace's behalf and tries slots 4 then 3 (it never uses COVERAGE_ID/slot 1, the
    slot conventionally held by coverage.py). So if only COVERAGE_ID (slot 1) is taken, ddtrace
    still collects coverage using slot 4.
    """
    import os
    from pathlib import Path
    import sys

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    # Claim COVERAGE_ID with a third-party tool — ddtrace must not be affected
    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "something_else")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.async_code import call_async_function_and_report_line_number

    ModuleCodeCollector.start_coverage()
    line_number = call_async_function_and_report_line_number()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))

    # ddtrace collects coverage normally — COVERAGE_ID being taken does not matter
    assert "tests/coverage/included_path/async_code.py" in executable, (
        f"Expected async_code.py in executable, got: {executable}"
    )
    assert len(executable["tests/coverage/included_path/async_code.py"]) > 0, (
        "Expected executable lines in async_code.py but got none"
    )
    assert "tests/coverage/included_path/async_code.py" in covered, f"Expected async_code.py in covered, got: {covered}"
    assert line_number == 7

    # Verify that the other tool's COVERAGE_ID slot is still intact
    assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) == "something_else"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring coverage is only used in Python 3.12+")
@pytest.mark.subprocess(check_logs=False)
def test_dd_tool_slot_clash_causes_graceful_degradation():
    """If both multiplexer candidate slots (4, 3) are taken, ddtrace logs a warning and skips coverage.

    Coverage routes through the shared multiplexer, which only tries slots 4 and 3 (it deliberately
    avoids COVERAGE_ID/slot 1 so it never collides with external coverage tools). If another tool
    already holds both 4 and 3, the multiplexer cannot claim a slot, coverage degrades gracefully,
    and no lines are instrumented.
    """
    import os
    from pathlib import Path
    import sys

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    # The multiplexer's candidate slots (ddtrace.internal.monitoring._CANDIDATE_TOOL_IDS).
    # Claim both before install — ddtrace must degrade gracefully.
    for slot in (4, 3):
        sys.monitoring.use_tool_id(slot, "something_else")

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

    # When no slot is acquired successfully, no lines are instrumented
    expected_executable = {
        "tests/coverage/included_path/async_code.py": set(),
    }
    expected_covered = {}
    expected_covered_with_imports = {}

    assert executable == expected_executable, (
        f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
    )
    assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
    assert covered_with_imports == expected_covered_with_imports, (
        f"Covered lines with imports mismatch: expected={expected_covered_with_imports} "
        f"vs actual={covered_with_imports}"
    )
    assert line_number == 7
