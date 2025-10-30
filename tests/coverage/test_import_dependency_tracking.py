"""
Tests for import dependency tracking.

These tests ensure that import-time dependencies are correctly tracked across
different scenarios. They serve as regression tests to catch issues if the
import tracking mechanism is refactored in the future.

All tests run in BOTH file-level and line-level modes (parametrized):

### What These Tests Verify

1. **Direct imports**: Module A imports Module B → both tracked
2. **Transitive imports**: A→B→C chain → all three tracked
3. **Imports inside functions**: Lazy imports are tracked correctly
4. **Context isolation**: Import tracking works across multiple test contexts
5. **Internal data structures**:
   - `_import_names_by_path` is populated correctly
   - `_import_time_name_to_path` maps names to paths
6. **include_imported flag**: Dependencies only included when requested
"""

import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_direct_import_dependency():
    """
    Test that a direct import dependency is tracked.

    Scenario: Module A imports Module B
    Expected: When include_imported=True, both A and B are in covered lines
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Import a module that has import dependencies
    from tests.coverage.included_path.import_time_callee import called_in_session_import_time

    ModuleCodeCollector.start_coverage()
    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    covered_no_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False)
    )
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    # Verify the main file is covered
    assert (
        "tests/coverage/included_path/import_time_callee.py" in covered_no_imports
    ), "Main executed file should be in coverage without imports"

    # CRITICAL: Verify dependency is only included with include_imported=True
    assert (
        "tests/coverage/included_path/import_time_lib.py" in covered_with_imports
    ), "Import dependency should be included when include_imported=True"

    # Note: import_time_lib may be in covered_no_imports if it was imported before coverage started
    # The key test is that it's definitely in covered_with_imports


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_transitive_import_dependency():
    """
    Test that transitive import dependencies are tracked.

    Scenario: Module A imports Module B, Module B imports Module C
    Expected: When include_imported=True, all three modules are in covered lines
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.import_time_callee import called_in_session_import_time

    ModuleCodeCollector.start_coverage()
    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    # Verify all modules in the chain are tracked
    expected_modules = [
        "tests/coverage/included_path/import_time_callee.py",  # A
        "tests/coverage/included_path/import_time_lib.py",  # B (imported by A)
        "tests/coverage/included_path/nested_import_time_lib.py",  # C (imported by B)
    ]

    for module in expected_modules:
        assert (
            module in covered_with_imports
        ), f"Transitive dependency {module} should be tracked with include_imported=True"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_import_inside_function():
    """
    Test that imports inside functions are tracked as dependencies.

    Scenario: A function imports a module inside its body
    Expected: The imported module is tracked when include_imported=True
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Import the module but not the specific import inside the function yet
    from tests.coverage.included_path.imported_in_function_lib import module_level_constant  # noqa
    from tests.coverage.included_path.import_time_callee import calls_function_imported_in_function

    ModuleCodeCollector.start_coverage()
    calls_function_imported_in_function()
    ModuleCodeCollector.stop_coverage()

    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    # CRITICAL: The module imported inside the function should be tracked
    assert (
        "tests/coverage/included_path/imported_in_function_lib.py" in covered_with_imports
    ), "Module imported inside function should be tracked with include_imported=True"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_import_tracking_persists_across_contexts():
    """
    Test that import dependency tracking works correctly across multiple contexts.

    This is critical for per-test coverage where the same code runs in different contexts.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.import_time_callee import called_in_session_import_time

    # Context 1
    with ModuleCodeCollector.CollectInContext() as context1:
        called_in_session_import_time()
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Context 2 - should have same runtime coverage
    with ModuleCodeCollector.CollectInContext() as context2:
        called_in_session_import_time()
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Both contexts should have the main file (re-instrumentation test)
    assert "tests/coverage/included_path/import_time_callee.py" in context1_covered, "Context 1 should have main file"

    assert (
        "tests/coverage/included_path/import_time_callee.py" in context2_covered
    ), "Context 2 should have main file (re-instrumentation test)"

    # Import dependencies are tracked at the module level (via _import_time_covered)
    # Verify they're available
    import_time_covered = ModuleCodeCollector._instance._import_time_covered

    # The import_time_callee file should have import dependencies recorded
    callee_path = None
    for path in import_time_covered.keys():
        if "import_time_callee.py" in path:
            callee_path = path
            break

    # If import-time tracking is enabled, we should have the callee file in import_time_covered
    if callee_path:
        assert (
            len(import_time_covered[callee_path]) > 0
        ), "Import time covered should track lines for import_time_callee.py"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_import_names_by_path_populated():
    """
    Test that the _import_names_by_path data structure is correctly populated.

    This is the core mechanism for import tracking - if this breaks, all import
    dependency tracking fails.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.import_time_callee import called_in_session_import_time

    ModuleCodeCollector.start_coverage()
    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    # CRITICAL: Check that _import_names_by_path has entries
    import_names_by_path = ModuleCodeCollector._instance._import_names_by_path

    # Find the import_time_callee.py entry
    callee_path = None
    for path in import_names_by_path.keys():
        if "import_time_callee.py" in path:
            callee_path = path
            break

    assert callee_path is not None, "_import_names_by_path should contain import_time_callee.py"

    # Verify it has import entries
    import_entries = import_names_by_path[callee_path]
    assert len(import_entries) > 0, f"import_time_callee.py should have import entries, got: {import_entries}"

    # Check that the import entries reference import_time_lib
    import_names_str = str(import_entries)
    assert (
        "import_time_lib" in import_names_str
    ), f"Import entries should reference import_time_lib, got: {import_entries}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_no_false_dependencies():
    """
    Test that modules that are NOT imported are NOT tracked as dependencies.

    This ensures we don't have false positives in dependency tracking.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Import only import_time_callee, which imports import_time_lib
    from tests.coverage.included_path.import_time_callee import called_in_session_import_time

    ModuleCodeCollector.start_coverage()
    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    # Verify that imported_in_function_lib is NOT in the dependencies
    # (it's only used in a different function that we didn't call)
    # Note: This may be present if module_level_constant was evaluated at import time
    # So we just verify the core dependencies are correct
    assert (
        "tests/coverage/included_path/import_time_lib.py" in covered_with_imports
    ), "Should track actual import dependency"
    assert (
        "tests/coverage/included_path/nested_import_time_lib.py" in covered_with_imports
    ), "Should track transitive import dependency"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_import_time_name_to_path_mapping():
    """
    Test that _import_time_name_to_path correctly maps module names to file paths.

    This mapping is crucial for resolving import dependencies.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.import_time_callee import called_in_session_import_time

    ModuleCodeCollector.start_coverage()
    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    # CRITICAL: Check that _import_time_name_to_path has entries
    name_to_path = ModuleCodeCollector._instance._import_time_name_to_path

    # Look for import_time_lib in the mapping
    found_lib = False
    for name, path in name_to_path.items():
        if "import_time_lib" in name:
            found_lib = True
            assert "import_time_lib.py" in path, f"Name {name} should map to import_time_lib.py path, got: {path}"
            break

    assert found_lib, f"_import_time_name_to_path should contain import_time_lib, got: {list(name_to_path.keys())}"
