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

    # Expected coverage for context 1 (includes constants_dynamic on first import)
    expected_context1 = {
        "tests/coverage/included_path/nested_fixture.py": {16, 17, 23, 25, 26, 31, 32, 33},
        "tests/coverage/included_path/layer2_toplevel.py": {10, 13, 15, 16},
        "tests/coverage/included_path/layer2_dynamic.py": {1, 4, 7, 9, 12, 13, 15, 16},
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {1, 4, 5, 6},
        "tests/coverage/included_path/constants_dynamic.py": {1, 4, 5},
    }

    # Expected coverage for context 2 (constants_dynamic not re-executed - Python caches modules)
    expected_context2 = {
        "tests/coverage/included_path/nested_fixture.py": {16, 17, 23, 25, 26, 31, 32, 33},
        "tests/coverage/included_path/layer2_toplevel.py": {10, 13, 15, 16},
        "tests/coverage/included_path/layer2_dynamic.py": {9, 12, 13, 15, 16},
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
    }

    # Use same dict for both modes - utility function extracts what it needs
    assert_coverage_matches(context1_covered, expected_context1, file_level_mode, "Context 1")
    assert_coverage_matches(context2_covered, expected_context2, file_level_mode, "Context 2")


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

    # Define expected coverage with line numbers (works for both file-level and line-level modes)
    # Context 1: First toplevel execution (includes import-time lines for layer3_dynamic)
    expected_context1 = {
        "tests/coverage/included_path/nested_fixture.py": {16, 17},
        "tests/coverage/included_path/layer2_toplevel.py": {10, 13, 15, 16},
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {1, 4, 5, 6},  # import-time + runtime
    }

    # Context 2: First dynamic execution (includes import-time lines for layer2_dynamic and constants_dynamic)
    expected_context2 = {
        "tests/coverage/included_path/nested_fixture.py": {23, 25, 26},
        "tests/coverage/included_path/layer2_dynamic.py": {1, 4, 7, 9, 12, 13, 15, 16},  # import-time + runtime
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
        "tests/coverage/included_path/constants_dynamic.py": {1, 4, 5},  # module-level constants
    }

    # Context 3: Second toplevel execution (runtime only - re-instrumentation test)
    expected_context3 = {
        "tests/coverage/included_path/nested_fixture.py": {16, 17},
        "tests/coverage/included_path/layer2_toplevel.py": {10, 13, 15, 16},
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
    }

    # Context 4: Second dynamic execution (runtime only, no constants_dynamic - re-instrumentation test)
    expected_context4 = {
        "tests/coverage/included_path/nested_fixture.py": {23, 25, 26},
        "tests/coverage/included_path/layer2_dynamic.py": {9, 12, 13, 15, 16},
        "tests/coverage/included_path/layer3_toplevel.py": {5, 6},
        "tests/coverage/included_path/layer3_dynamic.py": {5, 6},
    }

    # Use same dicts for both modes - utility function extracts what it needs
    assert_coverage_matches(context1_covered, expected_context1, file_level_mode, "Context 1")
    assert_coverage_matches(context2_covered, expected_context2, file_level_mode, "Context 2")
    assert_coverage_matches(context3_covered, expected_context3, file_level_mode, "Context 3")
    assert_coverage_matches(context4_covered, expected_context4, file_level_mode, "Context 4")
