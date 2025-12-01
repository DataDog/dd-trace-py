"""
Tests for import-time coverage tracking of constant-only modules.

These tests verify that modules containing only constants (no executable functions)
are properly tracked in import-time coverage, which is important for the Intelligent
Test Runner to understand code dependencies.
"""

import sys

import pytest


@pytest.mark.subprocess
def test_constants_module_toplevel_import_tracked():
    """
    Test that constant-only modules imported at top-level are tracked in import-time coverage.

    This verifies that even modules with no executable code (only constant declarations)
    appear in the import-time dependency tracking.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Import module that has top-level constant imports
    from tests.coverage.included_path.layer2_toplevel import layer2_toplevel_function

    ModuleCodeCollector.start_coverage()
    result = layer2_toplevel_function(5)
    ModuleCodeCollector.stop_coverage()

    assert result == 110  # Verify the function works correctly

    # Get coverage with and without imports
    covered = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False)  # type: ignore[union-attr]
    )
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)  # type: ignore[union-attr]
    )

    # Verify runtime coverage (without imports)
    assert "tests/coverage/included_path/layer2_toplevel.py" in covered
    assert "tests/coverage/included_path/layer3_toplevel.py" in covered

    # CRITICAL: Verify import-time coverage includes the constants module
    # Even though constants_toplevel.py has no executable code, it should appear
    # in import-time dependencies because layer2_toplevel imports from it
    assert "tests/coverage/included_path/constants_toplevel.py" in covered_with_imports, (
        "constants_toplevel.py missing from import-time coverage! "
        "Constant-only modules should be tracked as dependencies."
    )

    # The constants module should have its lines tracked
    constants_lines = covered_with_imports.get("tests/coverage/included_path/constants_toplevel.py", set())
    # Verify it includes the constant declarations (lines 4, 5, 6)
    expected_constant_lines = {4, 5, 6}
    assert expected_constant_lines.issubset(constants_lines), (
        f"Expected constant declaration lines {expected_constant_lines} in coverage, "
        f"but got: {sorted(constants_lines)}"
    )


@pytest.mark.subprocess
def test_constants_module_dynamic_import_tracked():
    """
    Test that constant-only modules imported dynamically are tracked in import-time coverage.

    This verifies that dynamically imported constant modules also appear in
    import-time dependency tracking.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Import module that has dynamic constant imports
    from tests.coverage.included_path.layer2_dynamic import layer2_dynamic_function

    ModuleCodeCollector.start_coverage()
    result = layer2_dynamic_function(5)
    ModuleCodeCollector.stop_coverage()

    assert result == 55  # Verify the function works correctly

    # Get coverage with and without imports
    covered = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False)  # type: ignore[union-attr]
    )
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)  # type: ignore[union-attr]
    )

    # Verify runtime coverage (without imports)
    assert "tests/coverage/included_path/layer2_dynamic.py" in covered

    # CRITICAL: Verify import-time coverage includes the dynamically imported constants module
    assert "tests/coverage/included_path/constants_dynamic.py" in covered_with_imports, (
        "constants_dynamic.py missing from import-time coverage! "
        "Dynamically imported constant-only modules should be tracked as dependencies."
    )

    # The constants module should have its lines tracked
    constants_lines = covered_with_imports.get("tests/coverage/included_path/constants_dynamic.py", set())
    # Verify it includes the constant declarations (lines 4, 5)
    expected_constant_lines = {4, 5}
    assert expected_constant_lines.issubset(constants_lines), (
        f"Expected constant declaration lines {expected_constant_lines} in coverage, "
        f"but got: {sorted(constants_lines)}"
    )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_constants_module_reinstrumentation():
    """
    Test that constant-only modules are properly re-instrumented between coverage collections.

    This ensures that constant modules appear consistently in import-time coverage
    across multiple start/stop cycles (important for per-test coverage in pytest).
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.layer2_toplevel import layer2_toplevel_function

    # First coverage collection
    ModuleCodeCollector.start_coverage()
    layer2_toplevel_function(5)
    ModuleCodeCollector.stop_coverage()

    first_covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)  # type: ignore[union-attr]
    )

    # Clear coverage to simulate new test
    ModuleCodeCollector._instance.covered.clear()  # type: ignore[union-attr]

    # Second coverage collection
    ModuleCodeCollector.start_coverage()
    layer2_toplevel_function(10)
    ModuleCodeCollector.stop_coverage()

    second_covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)  # type: ignore[union-attr]
    )

    # CRITICAL: Both collections should track the constants module
    assert (
        "tests/coverage/included_path/constants_toplevel.py" in first_covered_with_imports
    ), "First collection missing constants_toplevel.py"
    assert (
        "tests/coverage/included_path/constants_toplevel.py" in second_covered_with_imports
    ), "Second collection missing constants_toplevel.py - re-instrumentation failed for constant modules!"

    # Both should have the same lines for the constants module
    first_constants = first_covered_with_imports["tests/coverage/included_path/constants_toplevel.py"]
    second_constants = second_covered_with_imports["tests/coverage/included_path/constants_toplevel.py"]

    assert first_constants == second_constants, (
        f"Constants coverage differs between collections - re-instrumentation issue!\n"
        f"  First:  {sorted(first_constants)}\n"
        f"  Second: {sorted(second_constants)}"
    )

    # Verify the constants are actually tracked
    expected_lines = {4, 5, 6}
    assert expected_lines.issubset(
        second_constants
    ), f"Expected constant lines {expected_lines} in second collection, got: {sorted(second_constants)}"
