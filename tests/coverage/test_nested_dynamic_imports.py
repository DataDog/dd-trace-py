"""
Test complex nested import scenarios with multiple layers of top-level and dynamic imports.

This test checks if re-instrumentation works correctly when:
- Fixture code has top-level imports
- Fixture code has dynamic (function-level) imports
- Those imported modules themselves have more imports (both top-level and dynamic)
- Multiple contexts execute the same code paths

The fixture modules are in tests/coverage/included_path/:
- nested_fixture.py (main fixture with top-level and dynamic imports)
- layer2_toplevel.py, layer2_dynamic.py (imported by fixture)
- layer3_toplevel.py, layer3_dynamic.py (imported by layer2)
"""

import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_nested_imports_toplevel_path_reinstrumentation():
    """
    Test re-instrumentation with nested imports via top-level import path.

    This tests: fixture (top-level import) -> layer2 (top-level import) -> layer3
    And: fixture (top-level import) -> layer2 (dynamic import) -> layer3
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    from tests.coverage.included_path.nested_fixture import fixture_toplevel_path

    # Context 1: Execute the top-level path
    with ModuleCodeCollector.CollectInContext() as context1:
        result1 = fixture_toplevel_path(5)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Context 2: Execute the SAME path again - should get SAME coverage
    with ModuleCodeCollector.CollectInContext() as context2:
        result2 = fixture_toplevel_path(10)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Context 3: One more time
    with ModuleCodeCollector.CollectInContext() as context3:
        result3 = fixture_toplevel_path(3)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())

    # layer2_toplevel: layer3_toplevel(5) = 15, then layer3_dynamic(15) = (15+10)*2 = 50
    assert result1 == 50
    assert result2 == 80  # layer3_toplevel(10)=30, layer3_dynamic(30)=(30+10)*2=80
    assert result3 == 38  # layer3_toplevel(3)=9, layer3_dynamic(9)=(9+10)*2=38

    # Expected runtime lines (captured in all contexts)
    expected_runtime = {
        "tests/coverage/included_path/nested_fixture.py": {16, 17},
        "tests/coverage/included_path/layer2_toplevel.py": {9, 12, 14, 15},
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
    }

    # Expected import-time lines (only in context 1)
    expected_import_time = {
        "tests/coverage/included_path/layer3_dynamic.py": {1, 4},  # docstring + function def
    }

    for file_path, expected_lines in expected_runtime.items():
        # All contexts should have the file
        assert file_path in context1_covered, f"Context 1 missing {file_path}"
        assert file_path in context2_covered, f"Context 2 missing {file_path} - re-instrumentation failed!"
        assert file_path in context3_covered, f"Context 3 missing {file_path} - re-instrumentation failed!"

        # CRITICAL: Contexts 2 and 3 must have identical runtime coverage
        assert context2_covered[file_path] == context3_covered[file_path], (
            f"{file_path}: Contexts 2 and 3 differ - re-instrumentation inconsistent!\n"
            f"  Expected: {sorted(expected_lines)}\n"
            f"  Context 2: {sorted(context2_covered[file_path])}\n"
            f"  Context 3: {sorted(context3_covered[file_path])}"
        )

        # Check runtime lines are captured in contexts 2 and 3
        assert context2_covered[file_path] == expected_lines, (
            f"{file_path}: Runtime coverage mismatch\n"
            f"  Expected: {sorted(expected_lines)}\n"
            f"  Got: {sorted(context2_covered[file_path])}"
        )

        # Context 1 should have runtime + any import-time lines
        expected_context1 = expected_lines | expected_import_time.get(file_path, set())
        assert context1_covered[file_path] == expected_context1, (
            f"{file_path}: Context 1 coverage mismatch\n"
            f"  Expected: {sorted(expected_context1)}\n"
            f"  Got: {sorted(context1_covered[file_path])}"
        )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_nested_imports_dynamic_path_reinstrumentation():
    """
    Test re-instrumentation with nested imports via dynamic import path.

    This tests: fixture (dynamic import) -> layer2 (top-level import) -> layer3
    And: fixture (dynamic import) -> layer2 (dynamic import) -> layer3
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    from tests.coverage.included_path.nested_fixture import fixture_dynamic_path

    # Context 1: Execute the dynamic path
    with ModuleCodeCollector.CollectInContext() as context1:
        result1 = fixture_dynamic_path(5)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Context 2: Execute the SAME path again - should get SAME coverage
    with ModuleCodeCollector.CollectInContext() as context2:
        result2 = fixture_dynamic_path(10)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Context 3: One more time
    with ModuleCodeCollector.CollectInContext() as context3:
        result3 = fixture_dynamic_path(3)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())

    # layer2_dynamic: layer3_toplevel(5)=15, layer3_dynamic(15)=50, return 50+5=55
    assert result1 == 55
    assert result2 == 85  # layer3_toplevel(10)=30, layer3_dynamic(30)=80, return 85
    assert result3 == 43  # layer3_toplevel(3)=9, layer3_dynamic(9)=38, return 43

    # Expected runtime lines (captured in all contexts)
    expected_runtime = {
        "tests/coverage/included_path/nested_fixture.py": {23, 25, 26},
        "tests/coverage/included_path/layer2_dynamic.py": {9, 12, 14, 15},
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
    }

    # Expected import-time lines (only in context 1)
    expected_import_time = {
        "tests/coverage/included_path/layer2_dynamic.py": {1, 4, 7},  # docstring + import + function def
        "tests/coverage/included_path/layer3_dynamic.py": {1, 4},  # docstring + function def
    }

    for file_path, expected_lines in expected_runtime.items():
        # All contexts should have the file
        assert file_path in context1_covered, f"Context 1 missing {file_path}"
        assert file_path in context2_covered, f"Context 2 missing {file_path} - re-instrumentation failed!"
        assert file_path in context3_covered, f"Context 3 missing {file_path} - re-instrumentation failed!"

        # CRITICAL: Contexts 2 and 3 must have identical runtime coverage
        assert context2_covered[file_path] == context3_covered[file_path], (
            f"{file_path}: Contexts 2 and 3 differ - re-instrumentation inconsistent!\n"
            f"  Expected: {sorted(expected_lines)}\n"
            f"  Context 2: {sorted(context2_covered[file_path])}\n"
            f"  Context 3: {sorted(context3_covered[file_path])}"
        )

        # Check runtime lines are captured in contexts 2 and 3
        assert context2_covered[file_path] == expected_lines, (
            f"{file_path}: Runtime coverage mismatch\n"
            f"  Expected: {sorted(expected_lines)}\n"
            f"  Got: {sorted(context2_covered[file_path])}"
        )

        # Context 1 should have runtime + any import-time lines
        expected_context1 = expected_lines | expected_import_time.get(file_path, set())
        assert context1_covered[file_path] == expected_context1, (
            f"{file_path}: Context 1 coverage mismatch\n"
            f"  Expected: {sorted(expected_context1)}\n"
            f"  Got: {sorted(context1_covered[file_path])}"
        )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_nested_imports_mixed_path_reinstrumentation():
    """
    Test re-instrumentation with nested imports using both top-level and dynamic paths.

    This is the most comprehensive test - it exercises ALL import paths in sequence.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    from tests.coverage.included_path.nested_fixture import fixture_mixed_path

    # Context 1: Execute all paths
    with ModuleCodeCollector.CollectInContext() as context1:
        fixture_mixed_path(5)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Context 2: Execute all paths again
    with ModuleCodeCollector.CollectInContext() as context2:
        fixture_mixed_path(10)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Context 3: One more time
    with ModuleCodeCollector.CollectInContext() as context3:
        fixture_mixed_path(3)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())

    # Expected runtime lines (captured in all contexts) - mixed path uses BOTH toplevel and dynamic
    expected_runtime = {
        "tests/coverage/included_path/nested_fixture.py": {16, 17, 23, 25, 26, 31, 32, 33},
        "tests/coverage/included_path/layer2_toplevel.py": {9, 12, 14, 15},
        "tests/coverage/included_path/layer2_dynamic.py": {9, 12, 14, 15},
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
    }

    # Expected import-time lines (only in context 1)
    expected_import_time = {
        "tests/coverage/included_path/layer2_dynamic.py": {1, 4, 7},  # docstring + import + function def
        "tests/coverage/included_path/layer3_dynamic.py": {1, 4},  # docstring + function def
    }

    for file_path, expected_lines in expected_runtime.items():
        # All contexts should have the file
        assert file_path in context1_covered, f"Context 1 missing {file_path}"
        assert file_path in context2_covered, f"Context 2 missing {file_path} - re-instrumentation failed!"
        assert file_path in context3_covered, f"Context 3 missing {file_path} - re-instrumentation failed!"

        # CRITICAL: Contexts 2 and 3 must have identical runtime coverage
        assert context2_covered[file_path] == context3_covered[file_path], (
            f"{file_path}: Contexts 2 and 3 differ - re-instrumentation inconsistent!\n"
            f"  Expected: {sorted(expected_lines)}\n"
            f"  Context 2: {sorted(context2_covered[file_path])}\n"
            f"  Context 3: {sorted(context3_covered[file_path])}"
        )

        # Check runtime lines are captured in contexts 2 and 3
        assert context2_covered[file_path] == expected_lines, (
            f"{file_path}: Runtime coverage mismatch\n"
            f"  Expected: {sorted(expected_lines)}\n"
            f"  Got: {sorted(context2_covered[file_path])}"
        )

        # Context 1 should have runtime + any import-time lines
        expected_context1 = expected_lines | expected_import_time.get(file_path, set())
        assert context1_covered[file_path] == expected_context1, (
            f"{file_path}: Context 1 coverage mismatch\n"
            f"  Expected: {sorted(expected_context1)}\n"
            f"  Got: {sorted(context1_covered[file_path])}"
        )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_nested_imports_interleaved_execution():
    """
    Test re-instrumentation with interleaved execution of different import paths.

    This simulates a realistic scenario where different tests might call different
    code paths, and we need to ensure ALL paths are properly instrumented in each context.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    from tests.coverage.included_path.nested_fixture import fixture_dynamic_path
    from tests.coverage.included_path.nested_fixture import fixture_toplevel_path

    # Context 1: Execute toplevel path
    with ModuleCodeCollector.CollectInContext() as context1:
        fixture_toplevel_path(5)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Context 2: Execute dynamic path (different path)
    with ModuleCodeCollector.CollectInContext() as context2:
        fixture_dynamic_path(10)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Context 3: Execute toplevel path again (back to first path)
    with ModuleCodeCollector.CollectInContext() as context3:
        fixture_toplevel_path(3)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())

    # Context 4: Execute dynamic path again
    with ModuleCodeCollector.CollectInContext() as context4:
        fixture_dynamic_path(7)
        context4_covered = _get_relpath_dict(cwd_path, context4.get_covered_lines())

    # Expected coverage for contexts 1 and 3 (both use toplevel path)
    expected_toplevel_runtime = {
        "tests/coverage/included_path/nested_fixture.py": {16, 17},
        "tests/coverage/included_path/layer2_toplevel.py": {9, 12, 14, 15},
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
    }

    # Expected coverage for contexts 2 and 4 (both use dynamic path)
    expected_dynamic_runtime = {
        "tests/coverage/included_path/nested_fixture.py": {23, 25, 26},
        "tests/coverage/included_path/layer2_dynamic.py": {9, 12, 14, 15},
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
    }

    # Check toplevel path (contexts 1 and 3)
    for file_path, expected_lines in expected_toplevel_runtime.items():
        assert file_path in context1_covered, f"Context 1 missing {file_path}"
        assert file_path in context3_covered, f"Context 3 missing {file_path} - re-instrumentation failed!"

        # CRITICAL: Context 3 should have exact runtime coverage
        assert context3_covered[file_path] == expected_lines, (
            f"{file_path}: Context 3 runtime mismatch\n"
            f"  Expected: {sorted(expected_lines)}\n"
            f"  Got: {sorted(context3_covered[file_path])}"
        )

        # Context 1 may have import-time lines for layer3_dynamic (first dynamic import)
        if file_path == "tests/coverage/included_path/layer3_dynamic.py":
            # Context 1 captures import-time + runtime for dynamically imported module
            expected_context1 = expected_lines | {1, 4}  # docstring + function def
            assert context1_covered[file_path] == expected_context1, (
                f"{file_path}: Context 1 mismatch\n"
                f"  Expected: {sorted(expected_context1)}\n"
                f"  Got: {sorted(context1_covered[file_path])}"
            )
        else:
            assert context1_covered[file_path] == expected_lines, (
                f"{file_path}: Context 1 mismatch\n"
                f"  Expected: {sorted(expected_lines)}\n"
                f"  Got: {sorted(context1_covered[file_path])}"
            )

    # Check dynamic path (contexts 2 and 4)
    for file_path, expected_lines in expected_dynamic_runtime.items():
        assert file_path in context2_covered, f"Context 2 missing {file_path}"
        assert file_path in context4_covered, f"Context 4 missing {file_path} - re-instrumentation failed!"

        # CRITICAL: Context 4 should have exact runtime coverage (proves re-instrumentation works)
        assert context4_covered[file_path] == expected_lines, (
            f"{file_path}: Context 4 runtime mismatch\n"
            f"  Expected: {sorted(expected_lines)}\n"
            f"  Got: {sorted(context4_covered[file_path])}"
        )

        # Context 2 is first to use dynamic path, may have import-time lines
        # Note: layer3_dynamic was already imported in Context 1, so Context 2 won't have its import-time
        if file_path == "tests/coverage/included_path/layer2_dynamic.py":
            # Context 2 captures import-time for layer2_dynamic (first time it's imported)
            expected_context2 = expected_lines | {1, 4, 7}  # docstring + import + function def
            assert context2_covered[file_path] == expected_context2, (
                f"{file_path}: Context 2 mismatch\n"
                f"  Expected: {sorted(expected_context2)}\n"
                f"  Got: {sorted(context2_covered[file_path])}"
            )
        else:
            assert context2_covered[file_path] == expected_lines, (
                f"{file_path}: Context 2 mismatch\n"
                f"  Expected: {sorted(expected_lines)}\n"
                f"  Got: {sorted(context2_covered[file_path])}"
            )
