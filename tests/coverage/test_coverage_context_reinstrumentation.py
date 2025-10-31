"""
Regression tests for Python 3.12+ coverage re-instrumentation between contexts.

These tests verify that coverage collection properly re-instruments code between
different coverage contexts (e.g., between tests or suites). This is critical
for the DISABLE optimization in Python 3.12+ where monitoring is disabled after
each line is recorded, and must be re-enabled for subsequent contexts.

The tests are intentionally high-level to survive implementation changes while
ensuring:
1. Each context gets complete coverage data
2. No coverage gaps occur between contexts
3. Code executed in multiple contexts is properly tracked in each
4. Loops and repeated execution don't prevent coverage in new contexts
"""

import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_sequential_contexts_with_no_overlap():
    """
    This is a regression test for the re-instrumentation mechanism. Without proper
    re-enablement of monitoring between contexts, subsequent contexts could miss
    coverage for lines that were already executed in previous contexts,
    or leak coverage to the next context.
    """
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

    # Import the functions we'll test
    from tests.coverage.included_path.callee import called_in_context_main
    from tests.coverage.included_path.callee import called_in_session_main

    # Context 1: Execute code and collect coverage for both functions
    with ModuleCodeCollector.CollectInContext() as both_contexts:
        called_in_session_main(1, 2)
        called_in_context_main(3, 4)
        both_contexts_covered = _get_relpath_dict(cwd_path, both_contexts.get_covered_lines())

    # Context 2: Execute only the context code
    with ModuleCodeCollector.CollectInContext() as context_context:
        called_in_context_main(3, 4)
        context_context_covered = _get_relpath_dict(cwd_path, context_context.get_covered_lines())

    # Context 3: Execute only the session code
    with ModuleCodeCollector.CollectInContext() as session_context:
        called_in_session_main(1, 2)
        session_context_covered = _get_relpath_dict(cwd_path, session_context.get_covered_lines())

    # Define expected coverage for each context
    # Context 1 executes both functions, so it covers all three files
    expected_both_contexts = {
        "tests/coverage/included_path/callee.py": {2, 3, 5, 6, 10, 11, 13, 14},
        "tests/coverage/included_path/lib.py": {1, 2, 5},
        "tests/coverage/included_path/in_context_lib.py": {1, 2, 5},
    }

    # Context 2 executes only called_in_context_main, so it covers callee.py and in_context_lib.py
    expected_context_context = {
        "tests/coverage/included_path/callee.py": {10, 11, 13, 14},
        "tests/coverage/included_path/in_context_lib.py": {2},
    }

    # Context 3 executes only called_in_session_main, so it covers callee.py and lib.py
    expected_session_context = {
        "tests/coverage/included_path/callee.py": {2, 3, 5, 6},
        "tests/coverage/included_path/lib.py": {2},
    }

    # Use utility function to verify coverage matches expectations
    assert_coverage_matches(both_contexts_covered, expected_both_contexts, file_level_mode, "Context 1 (both)")
    assert_coverage_matches(context_context_covered, expected_context_context, file_level_mode, "Context 2 (context)")
    assert_coverage_matches(session_context_covered, expected_session_context, file_level_mode, "Context 3 (session)")

    if not file_level_mode:
        # In line-level mode, add critical re-instrumentation checks
        # Line 2 is the function body - it MUST be present in contexts that execute the function
        assert 2 in both_contexts_covered["tests/coverage/included_path/lib.py"], "Context 1 missing lib.py line 2"
        assert (
            2 in session_context_covered["tests/coverage/included_path/lib.py"]
        ), "Context 3 missing lib.py line 2 - re-instrumentation failed!"

        assert (
            2 in both_contexts_covered["tests/coverage/included_path/in_context_lib.py"]
        ), "Context 1 missing in_context_lib.py line 2"
        assert (
            2 in context_context_covered["tests/coverage/included_path/in_context_lib.py"]
        ), "Context 2 missing in_context_lib.py line 2 - re-instrumentation failed!"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_context_with_repeated_execution_reinstruments_correctly():
    """
    Test that repeatedly executed code properly re-instruments between contexts.

    This ensures that the DISABLE optimization (which prevents repeated callbacks for the same
    line within a context) doesn't prevent coverage collection in subsequent contexts.
    """
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

    # Import functions that will be called multiple times
    from tests.coverage.included_path.lib import called_in_session

    # Context 1: Execute function multiple times within the context
    with ModuleCodeCollector.CollectInContext() as context1:
        # Call the same function multiple times - DISABLE should prevent
        # multiple callbacks within this context, but lines should still be recorded once
        for i in range(3):
            result1 = called_in_session(i, i + 1)
            assert result1 == (i, i + 1)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Context 2: Execute the SAME function again (multiple times)
    with ModuleCodeCollector.CollectInContext() as context2:
        for i in range(5):
            result2 = called_in_session(i * 2, i * 3)
            assert result2 == (i * 2, i * 3)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Verify both contexts captured lib.py
    expected_coverage = {"tests/coverage/included_path/lib.py": {2}}

    assert_coverage_matches(context1_covered, expected_coverage, file_level_mode, "Context 1")
    assert_coverage_matches(context2_covered, expected_coverage, file_level_mode, "Context 2")


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_nested_contexts_maintain_independence():
    """
    Test that nested coverage contexts maintain independence and proper re-instrumentation.

    This ensures the context stack properly handles re-instrumentation when entering
    nested contexts.

    IMPORTANT NOTE: The overlapping coverage does not get tracked by the outer context
    """
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

    from tests.coverage.included_path.callee import called_in_context_main
    from tests.coverage.included_path.callee import called_in_session_main

    # Outer context
    with ModuleCodeCollector.CollectInContext() as outer_context:
        called_in_session_main(1, 2)

        # Inner nested context - should capture everything independently
        with ModuleCodeCollector.CollectInContext() as inner_context:
            called_in_context_main(3, 4)
            inner_covered = _get_relpath_dict(cwd_path, inner_context.get_covered_lines())

        # Execute more code in outer context after inner completes
        called_in_context_main(3, 4)  # NOTE: This is not tracked as overlaps with inner
        outer_covered = _get_relpath_dict(cwd_path, outer_context.get_covered_lines())

    # Define expected coverage based on mode
    expected_inner = {
        "tests/coverage/included_path/callee.py": {10, 11, 13, 14},
        "tests/coverage/included_path/in_context_lib.py": {1, 2, 5},
    }
    expected_outer = {
        "tests/coverage/included_path/callee.py": {2, 3, 5, 6},
        "tests/coverage/included_path/lib.py": {1, 2, 5},
    }

    # Use utility function to assert based on coverage mode
    assert_coverage_matches(inner_covered, expected_inner, file_level_mode, "Inner context")
    assert_coverage_matches(outer_covered, expected_outer, file_level_mode, "Outer context")


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_many_sequential_contexts_no_degradation():
    """
    Test that coverage quality doesn't degrade over many sequential contexts.

    This is a stress test to ensure the re-instrumentation mechanism works
    consistently across many contexts without accumulating issues.
    """
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

    from tests.coverage.included_path.callee import called_in_session_main

    # Collect coverage from multiple sequential contexts
    all_context_coverages = []

    for i in range(3):
        with ModuleCodeCollector.CollectInContext() as context:
            called_in_session_main(i, i + 1)
            context_covered = _get_relpath_dict(cwd_path, context.get_covered_lines())
        all_context_coverages.append(context_covered)

    # Expected coverage: calls called_in_session_main, so should have callee.py and lib.py
    expected_files = {"tests/coverage/included_path/callee.py", "tests/coverage/included_path/lib.py"}
    expected_callee_runtime_lines = {2, 3, 5, 6}

    # Verify all contexts got complete coverage
    for idx, context_covered in enumerate(all_context_coverages):
        if file_level_mode:
            # In file-level mode, verify the expected files are present
            assert_coverage_matches(context_covered, expected_files, file_level_mode, f"Context {idx}")
        else:
            # In line-level mode, check that runtime lines are present (may have import-time lines too)
            actual_callee = context_covered["tests/coverage/included_path/callee.py"]
            assert expected_callee_runtime_lines.issubset(
                actual_callee
            ), f"Context {idx} missing expected callee lines: {expected_callee_runtime_lines}"

            # Check lib.py exists and has line 2 (the function body) - critical re-instrumentation check
            assert (
                2 in context_covered["tests/coverage/included_path/lib.py"]
            ), f"Context {idx} missing lib.py line 2 - re-instrumentation failed!"

    if not file_level_mode:
        # Critical: Coverage should not decrease over iterations
        # All contexts should have the same runtime lines for callee.py
        first_callee = all_context_coverages[0].get("tests/coverage/included_path/callee.py", set())
        last_callee = all_context_coverages[-1].get("tests/coverage/included_path/callee.py", set())

        # Check that expected_callee_lines are in both first and last
        assert expected_callee_runtime_lines.issubset(first_callee) and expected_callee_runtime_lines.issubset(
            last_callee
        ), f"Coverage degraded: first had {first_callee}, last had {last_callee}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_context_after_session_coverage():
    """
    Test that context-based coverage works correctly after session-level coverage.

    This ensures that transitioning from session coverage to context coverage
    properly re-instruments the code.
    """
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

    from tests.coverage.included_path.callee import called_in_context_main
    from tests.coverage.included_path.callee import called_in_session_main

    # Session-level coverage
    ModuleCodeCollector.start_coverage()
    called_in_session_main(1, 2)
    ModuleCodeCollector.stop_coverage()

    session_covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines())  # type: ignore[union-attr]

    # Now use context-based coverage - should still get complete coverage
    with ModuleCodeCollector.CollectInContext() as context1:
        called_in_session_main(3, 4)
        called_in_context_main(5, 6)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Another context - should also get complete coverage
    with ModuleCodeCollector.CollectInContext() as context2:
        called_in_session_main(7, 8)
        called_in_context_main(9, 10)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Define expected coverage
    # Session only calls called_in_session_main, so it should have callee.py and lib.py
    expected_session = {
        "tests/coverage/included_path/callee.py": {2, 3, 5, 6},
        "tests/coverage/included_path/lib.py": {1, 2, 5},
    }

    # Both contexts call both functions, so should have all three files
    expected_context = {
        "tests/coverage/included_path/callee.py": {2, 3, 5, 6, 10, 11, 13, 14},
        "tests/coverage/included_path/lib.py": {2},
        "tests/coverage/included_path/in_context_lib.py": {2},
    }

    # Use utility to verify coverage (with subset check for session since it may have import-time lines)
    if file_level_mode:
        # In file-level mode, verify files are present
        assert_coverage_matches(session_covered, expected_session, file_level_mode, "Session")
        assert_coverage_matches(context1_covered, expected_context, file_level_mode, "Context 1")
        assert_coverage_matches(context2_covered, expected_context, file_level_mode, "Context 2")
    else:
        # In line-level mode, check that expected runtime lines are present
        # Session may have import-time lines, so use subset check
        expected_session_runtime = expected_session["tests/coverage/included_path/callee.py"]
        assert expected_session_runtime.issubset(
            session_covered["tests/coverage/included_path/callee.py"]
        ), f"Session missing expected lines: {expected_session_runtime}"
        assert 2 in session_covered["tests/coverage/included_path/lib.py"], "Session missing lib.py line 2"

        # Contexts should have exact coverage
        expected_context_callee_runtime = expected_context["tests/coverage/included_path/callee.py"]
        assert expected_context_callee_runtime.issubset(
            context1_covered["tests/coverage/included_path/callee.py"]
        ), f"Context 1 missing expected lines: {expected_context_callee_runtime}"
        assert 2 in context1_covered["tests/coverage/included_path/lib.py"], "Context 1 missing lib.py line 2"
        assert (
            2 in context1_covered["tests/coverage/included_path/in_context_lib.py"]
        ), "Context 1 missing in_context_lib.py line 2"

        # Context 2 is the critical re-instrumentation check
        assert expected_context_callee_runtime.issubset(
            context2_covered["tests/coverage/included_path/callee.py"]
        ), f"Context 2 missing expected lines: {expected_context_callee_runtime}"
        assert (
            2 in context2_covered["tests/coverage/included_path/lib.py"]
        ), "Context 2 missing lib.py line 2 - re-instrumentation failed!"
        assert (
            2 in context2_covered["tests/coverage/included_path/in_context_lib.py"]
        ), "Context 2 missing in_context_lib.py line 2 - re-instrumentation failed!"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_comprehensive_reinstrumentation_with_simple_module():
    """
    Comprehensive test using a simple controlled module to verify re-instrumentation.

    This test uses a dedicated test module with predictable line numbers to ensure
    re-instrumentation works correctly across various code patterns.
    """
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

    from tests.coverage.included_path.reinstrumentation_test_module import function_with_branches
    from tests.coverage.included_path.reinstrumentation_test_module import function_with_loop
    from tests.coverage.included_path.reinstrumentation_test_module import multi_line_function
    from tests.coverage.included_path.reinstrumentation_test_module import simple_function

    # Context 1: Execute all functions
    with ModuleCodeCollector.CollectInContext() as context1:
        simple_function(1, 2)
        function_with_loop(5)
        function_with_branches(True)
        multi_line_function(2, 3, 4)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Context 2: Execute the same functions with different arguments
    with ModuleCodeCollector.CollectInContext() as context2:
        simple_function(10, 20)
        function_with_loop(10)
        function_with_branches(True)
        multi_line_function(5, 6, 7)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Context 3: Execute with different branch paths
    with ModuleCodeCollector.CollectInContext() as context3:
        simple_function(100, 200)
        function_with_loop(3)
        function_with_branches(False)  # Different branch
        multi_line_function(1, 1, 1)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())

    module_path = "tests/coverage/included_path/reinstrumentation_test_module.py"

    # Expected lines for context 1 and 2 (same branch in function_with_branches)
    expected_lines_true_branch = {11, 12, 17, 18, 19, 20, 25, 26, 29, 34, 35, 36, 37, 38, 39}

    # Expected lines for context 3 (false branch in function_with_branches)
    expected_lines_false_branch = {11, 12, 17, 18, 19, 20, 25, 28, 29, 34, 35, 36, 37, 38, 39}

    # Verify contexts 1 and 2 captured the true branch
    assert_coverage_matches(context1_covered, {module_path: expected_lines_true_branch}, file_level_mode, "Context 1")
    assert_coverage_matches(context2_covered, {module_path: expected_lines_true_branch}, file_level_mode, "Context 2")

    # Verify context 3 captured the false branch
    assert_coverage_matches(context3_covered, {module_path: expected_lines_false_branch}, file_level_mode, "Context 3")
