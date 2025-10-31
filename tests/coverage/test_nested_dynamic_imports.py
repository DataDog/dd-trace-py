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
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_nested_imports_mixed_path_reinstrumentation():
    """
    Test re-instrumentation with nested imports using both top-level and dynamic paths.

    This is the most comprehensive test - it exercises ALL import paths in sequence.
    """
    # DEV: Required local imports for subprocess decorator
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict
    from tests.coverage.utils import assert_coverage_matches

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")
    file_level_mode = os.getenv("_DD_COVERAGE_FILE_LEVEL") == "true"

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

    # Expected files for context 1 (includes constants_dynamic on first import)
    expected_files_context1 = {
        "tests/coverage/included_path/nested_fixture.py",
        "tests/coverage/included_path/layer2_toplevel.py",
        "tests/coverage/included_path/layer2_dynamic.py",
        "tests/coverage/included_path/layer3_toplevel.py",
        "tests/coverage/included_path/layer3_dynamic.py",
        "tests/coverage/included_path/constants_dynamic.py",
    }
    
    # Expected files for context 2 (constants_dynamic not re-executed)
    expected_files_context2 = {
        "tests/coverage/included_path/nested_fixture.py",
        "tests/coverage/included_path/layer2_toplevel.py",
        "tests/coverage/included_path/layer2_dynamic.py",
        "tests/coverage/included_path/layer3_toplevel.py",
        "tests/coverage/included_path/layer3_dynamic.py",
    }

    if file_level_mode:
        # In file-level mode, use utility to verify files have coverage
        assert_coverage_matches(context1_covered, expected_files_context1, file_level_mode, "Context 1")
        assert_coverage_matches(context2_covered, expected_files_context2, file_level_mode, "Context 2")
    else:
        # In line-level mode, check specific lines
        # Expected runtime lines for context 2 (no constants_dynamic - not re-executed)
        expected_runtime_context2 = {
            "tests/coverage/included_path/nested_fixture.py": {16, 17, 23, 25, 26, 31, 32, 33},
            "tests/coverage/included_path/layer2_toplevel.py": {10, 13, 15, 16},
            "tests/coverage/included_path/layer2_dynamic.py": {9, 12, 13, 15, 16},
            "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
            "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
        }

        # Expected import-time lines (only in context 1)
        expected_import_time = {
            "tests/coverage/included_path/layer2_dynamic.py": {1, 4, 7},  # docstring + import + function def
            "tests/coverage/included_path/layer3_dynamic.py": {1, 4},  # docstring + function def
            "tests/coverage/included_path/constants_dynamic.py": {1, 4, 5},  # docstring + constant def + usage
        }

        # Context 2 should have only runtime lines (re-instrumentation test)
        assert_coverage_matches(context2_covered, expected_runtime_context2, file_level_mode, "Context 2")

        # Context 1 should have runtime + any import-time lines
        expected_context1 = {
            file_path: expected_lines | expected_import_time.get(file_path, set())
            for file_path, expected_lines in expected_runtime_context2.items()
        }
        # Add constants_dynamic with all its lines (import-time)
        expected_context1["tests/coverage/included_path/constants_dynamic.py"] = expected_import_time[
            "tests/coverage/included_path/constants_dynamic.py"
        ]
        assert_coverage_matches(context1_covered, expected_context1, file_level_mode, "Context 1")

        # Verify import-time lines are NOT in context 2 (for files that are in context 2)
        for file_path, expected_lines in expected_import_time.items():
            if file_path in context2_covered:
                assert not expected_lines.issubset(context2_covered[file_path]), (
                    f"{file_path}: Import time not expected in Context 2 coverage\n" f"  Got: {expected_lines}"
                )


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_nested_imports_interleaved_execution():
    """
    Test re-instrumentation with interleaved execution of different import paths.

    This simulates a realistic scenario where different tests might call different
    code paths, and we need to ensure ALL paths are properly instrumented in each context.
    """
    # DEV: Required local imports for subprocess decorator
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict
    from tests.coverage.utils import assert_coverage_matches

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")
    file_level_mode = os.getenv("_DD_COVERAGE_FILE_LEVEL") == "true"

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

    # Expected files for each path
    expected_toplevel_files = {
        "tests/coverage/included_path/nested_fixture.py",
        "tests/coverage/included_path/layer2_toplevel.py",
        "tests/coverage/included_path/layer3_toplevel.py",
        "tests/coverage/included_path/layer3_dynamic.py",
    }

    # Context 2 includes constants_dynamic (first dynamic path execution)
    expected_dynamic_files_context2 = {
        "tests/coverage/included_path/nested_fixture.py",
        "tests/coverage/included_path/layer2_dynamic.py",
        "tests/coverage/included_path/layer3_toplevel.py",
        "tests/coverage/included_path/layer3_dynamic.py",
        "tests/coverage/included_path/constants_dynamic.py",
    }
    
    # Context 4 doesn't re-execute constants_dynamic
    expected_dynamic_files_context4 = {
        "tests/coverage/included_path/nested_fixture.py",
        "tests/coverage/included_path/layer2_dynamic.py",
        "tests/coverage/included_path/layer3_toplevel.py",
        "tests/coverage/included_path/layer3_dynamic.py",
    }

    if file_level_mode:
        # In file-level mode, use utility to verify files are present
        assert_coverage_matches(context1_covered, expected_toplevel_files, file_level_mode, "Context 1")
        assert_coverage_matches(context3_covered, expected_toplevel_files, file_level_mode, "Context 3")
        assert_coverage_matches(context2_covered, expected_dynamic_files_context2, file_level_mode, "Context 2")
        assert_coverage_matches(context4_covered, expected_dynamic_files_context4, file_level_mode, "Context 4")
    else:
        # In line-level mode, check specific lines
        # Expected coverage for contexts 1 and 3 (both use toplevel path)
        expected_toplevel_runtime = {
            "tests/coverage/included_path/nested_fixture.py": {16, 17},
            "tests/coverage/included_path/layer2_toplevel.py": {10, 13, 15, 16},
            "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
            "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
        }

        # Expected coverage for context 4 (no constants_dynamic - not re-executed)
        expected_dynamic_runtime_context4 = {
            "tests/coverage/included_path/nested_fixture.py": {23, 25, 26},
            "tests/coverage/included_path/layer2_dynamic.py": {9, 12, 13, 15, 16},
            "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
            "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
        }

        # CRITICAL: Context 3 should have exact runtime coverage (re-instrumentation test)
        assert_coverage_matches(context3_covered, expected_toplevel_runtime, file_level_mode, "Context 3")
        
        # CRITICAL: Context 4 should have exact runtime coverage (re-instrumentation test)
        assert_coverage_matches(context4_covered, expected_dynamic_runtime_context4, file_level_mode, "Context 4")

        # Check context 1 (may have import-time lines)
        for file_path, expected_lines in expected_toplevel_runtime.items():
            assert file_path in context1_covered, f"Context 1 missing {file_path}"

            # Context 1 may have import-time lines for dynamically imported modules
            if file_path == "tests/coverage/included_path/layer3_dynamic.py":
                # Context 1 captures import-time + runtime for layer3_dynamic (dynamically imported)
                expected_context1 = expected_lines | {1, 4}  # docstring + function def
                assert context1_covered[file_path] == expected_context1, (
                    f"{file_path}: Context 1 mismatch\n"
                    f"  Expected: {sorted(expected_context1)}\n"
                    f"  Got: {sorted(context1_covered[file_path])}"
                )
            elif file_path == "tests/coverage/included_path/layer2_toplevel.py":
                # layer2_toplevel is imported at fixture top-level, so it's imported before Context 1
                # Therefore, Context 1 won't have its import-time lines
                assert context1_covered[file_path] == expected_lines, (
                    f"{file_path}: Context 1 mismatch\n"
                    f"  Expected: {sorted(expected_lines)}\n"
                    f"  Got: {sorted(context1_covered[file_path])}"
                )
            else:
                assert context1_covered[file_path] == expected_lines, (
                    f"{file_path}: Context 1 mismatch\n"
                    f"  Expected: {sorted(expected_lines)}\n"
                    f"  Got: {sorted(context1_covered[file_path])}"
                )

        # Check context 2 (may have import-time lines, first dynamic path execution)
        for file_path, expected_lines in expected_dynamic_runtime_context4.items():
            assert file_path in context2_covered, f"Context 2 missing {file_path}"

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
        
        # Context 2 also has constants_dynamic (first time it's imported)
        assert "tests/coverage/included_path/constants_dynamic.py" in context2_covered
        assert context2_covered["tests/coverage/included_path/constants_dynamic.py"] == {1, 4, 5}, (
            "Context 2 constants_dynamic.py mismatch"
        )
