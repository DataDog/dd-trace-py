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
@pytest.mark.subprocess
def test_sequential_contexts_get_complete_coverage():
    """
    Test that multiple sequential coverage contexts each collect complete coverage.

    This is a regression test for the re-instrumentation mechanism. Without proper
    re-enablement of monitoring between contexts, subsequent contexts would miss
    coverage for lines that were already executed in previous contexts.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    # Import the functions we'll test
    from tests.coverage.included_path.callee import called_in_context_main
    from tests.coverage.included_path.callee import called_in_session_main

    # Context 1: Execute code and collect coverage
    with ModuleCodeCollector.CollectInContext() as context1:
        called_in_session_main(1, 2)
        called_in_context_main(3, 4)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Context 2: Execute THE SAME code and collect coverage
    # This should get the SAME coverage as context1, not reduced coverage
    with ModuleCodeCollector.CollectInContext() as context2:
        called_in_session_main(1, 2)
        called_in_context_main(3, 4)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Context 3: One more time to ensure it works consistently
    with ModuleCodeCollector.CollectInContext() as context3:
        called_in_session_main(1, 2)
        called_in_context_main(3, 4)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())

    # Expected coverage for callee.py (the code that actually executes in the function calls)
    # Note: lib.py and in_context_lib.py lines 1 and 5 are function definitions that only
    # execute at import time, so they appear in context1 but not in subsequent contexts
    expected_callee_lines = {2, 3, 5, 6, 10, 11, 13, 14}

    # All three contexts should have identical coverage for the main code paths
    assert "tests/coverage/included_path/callee.py" in context1_covered
    assert "tests/coverage/included_path/callee.py" in context2_covered
    assert "tests/coverage/included_path/callee.py" in context3_covered

    assert (
        context1_covered["tests/coverage/included_path/callee.py"] == expected_callee_lines
    ), f"Context 1 callee.py mismatch: expected={expected_callee_lines} vs actual={context1_covered['tests/coverage/included_path/callee.py']}"

    assert (
        context2_covered["tests/coverage/included_path/callee.py"] == expected_callee_lines
    ), f"Context 2 callee.py mismatch: expected={expected_callee_lines} vs actual={context2_covered['tests/coverage/included_path/callee.py']}"

    assert (
        context3_covered["tests/coverage/included_path/callee.py"] == expected_callee_lines
    ), f"Context 3 callee.py mismatch: expected={expected_callee_lines} vs actual={context3_covered['tests/coverage/included_path/callee.py']}"

    # Critical assertion: All contexts should capture function body execution
    # The key test is that lib.py line 2 (function body) appears in ALL contexts
    assert "tests/coverage/included_path/lib.py" in context1_covered
    assert "tests/coverage/included_path/lib.py" in context2_covered
    assert "tests/coverage/included_path/lib.py" in context3_covered

    # Line 2 is the function body - it MUST be in all contexts
    assert 2 in context1_covered["tests/coverage/included_path/lib.py"], "Context 1 missing lib.py line 2"
    assert (
        2 in context2_covered["tests/coverage/included_path/lib.py"]
    ), "Context 2 missing lib.py line 2 - re-instrumentation failed!"
    assert (
        2 in context3_covered["tests/coverage/included_path/lib.py"]
    ), "Context 3 missing lib.py line 2 - re-instrumentation failed!"

    # Same for in_context_lib.py
    assert (
        2 in context1_covered["tests/coverage/included_path/in_context_lib.py"]
    ), "Context 1 missing in_context_lib.py line 2"
    assert (
        2 in context2_covered["tests/coverage/included_path/in_context_lib.py"]
    ), "Context 2 missing in_context_lib.py line 2 - re-instrumentation failed!"
    assert (
        2 in context3_covered["tests/coverage/included_path/in_context_lib.py"]
    ), "Context 3 missing in_context_lib.py line 2 - re-instrumentation failed!"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
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

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    # Import functions that will be called multiple times
    from tests.coverage.included_path.lib import called_in_session

    # Context 1: Execute function multiple times within the context
    with ModuleCodeCollector.CollectInContext() as context1:
        # Call the same function multiple times - DISABLE should prevent
        # multiple callbacks within this context, but lines should still be recorded once
        for i in range(10):
            result1 = called_in_session(i, i + 1)
            assert result1 == (i, i + 1)
        context1_covered = _get_relpath_dict(cwd_path, context1.get_covered_lines())

    # Context 2: Execute the SAME function again (multiple times)
    with ModuleCodeCollector.CollectInContext() as context2:
        for i in range(5):
            result2 = called_in_session(i * 2, i * 3)
            assert result2 == (i * 2, i * 3)
        context2_covered = _get_relpath_dict(cwd_path, context2.get_covered_lines())

    # Context 3: Execute once more
    with ModuleCodeCollector.CollectInContext() as context3:
        for i in range(15):
            called_in_session(i, i)
        context3_covered = _get_relpath_dict(cwd_path, context3.get_covered_lines())

    # Expected coverage for lib.py (lines in called_in_session function)
    expected_lib_lines = {2}

    # All contexts should capture the same lines in lib.py
    assert (
        context1_covered.get("tests/coverage/included_path/lib.py") == expected_lib_lines
    ), f"Context 1 lib.py coverage: {context1_covered.get('tests/coverage/included_path/lib.py')}"

    assert (
        context2_covered.get("tests/coverage/included_path/lib.py") == expected_lib_lines
    ), f"Context 2 lib.py coverage: {context2_covered.get('tests/coverage/included_path/lib.py')}"

    assert (
        context3_covered.get("tests/coverage/included_path/lib.py") == expected_lib_lines
    ), f"Context 3 lib.py coverage: {context3_covered.get('tests/coverage/included_path/lib.py')}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_nested_contexts_maintain_independence():
    """
    Test that nested coverage contexts maintain independence and proper re-instrumentation.

    This ensures the context stack properly handles re-instrumentation when entering
    nested contexts.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

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
        called_in_session_main(5, 6)
        outer_covered = _get_relpath_dict(cwd_path, outer_context.get_covered_lines())

    # Inner context should have captured its specific execution
    expected_inner = {
        "tests/coverage/included_path/callee.py": {10, 11, 13, 14},
        "tests/coverage/included_path/in_context_lib.py": {1, 2, 5},
    }

    # Outer context should have both calls to called_in_session_main
    # (Note: may not include inner context code depending on implementation)
    assert "tests/coverage/included_path/callee.py" in outer_covered
    assert "tests/coverage/included_path/lib.py" in outer_covered

    # Inner context should have complete coverage for its execution
    assert (
        inner_covered == expected_inner
    ), f"Inner context coverage mismatch: expected={expected_inner} vs actual={inner_covered}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
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

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    from tests.coverage.included_path.callee import called_in_session_main

    # Collect coverage from multiple sequential contexts
    all_context_coverages = []

    for i in range(10):
        with ModuleCodeCollector.CollectInContext() as context:
            called_in_session_main(i, i + 1)
            context_covered = _get_relpath_dict(cwd_path, context.get_covered_lines())
        all_context_coverages.append(context_covered)

    # Expected coverage for callee.py - the runtime execution lines
    expected_callee_lines = {2, 3, 5, 6}

    # Verify all contexts got the same coverage for callee.py
    for idx, context_covered in enumerate(all_context_coverages):
        assert "tests/coverage/included_path/callee.py" in context_covered, f"Context {idx} missing callee.py"

        # Check callee.py lines match (these are runtime, not import-time)
        actual_callee = context_covered["tests/coverage/included_path/callee.py"]
        if idx == 0:
            # First context includes import lines
            assert expected_callee_lines.issubset(actual_callee), f"Context {idx} missing expected callee lines"
        else:
            # Subsequent contexts should have at least the runtime lines
            assert expected_callee_lines.issubset(actual_callee), f"Context {idx} missing expected callee lines"

        # Check lib.py exists and has line 2 (the function body)
        assert "tests/coverage/included_path/lib.py" in context_covered, f"Context {idx} missing lib.py"
        assert (
            2 in context_covered["tests/coverage/included_path/lib.py"]
        ), f"Context {idx} missing lib.py line 2 - re-instrumentation failed!"

    # Critical: Coverage should not decrease over iterations
    # All contexts should have the same runtime lines for callee.py
    first_callee = all_context_coverages[0].get("tests/coverage/included_path/callee.py", set())
    last_callee = all_context_coverages[-1].get("tests/coverage/included_path/callee.py", set())

    # Check that expected_callee_lines are in both first and last
    assert expected_callee_lines.issubset(first_callee) and expected_callee_lines.issubset(
        last_callee
    ), f"Coverage degraded: first had {first_callee}, last had {last_callee}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
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

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

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

    # Session should have captured called_in_session_main (runtime lines)
    expected_session_runtime = {2, 3, 5, 6}

    # Contexts should have both functions (runtime lines)
    expected_context_callee_runtime = {2, 3, 5, 6, 10, 11, 13, 14}

    # Verify session coverage
    assert "tests/coverage/included_path/callee.py" in session_covered
    assert expected_session_runtime.issubset(session_covered["tests/coverage/included_path/callee.py"])
    assert 2 in session_covered["tests/coverage/included_path/lib.py"], "Session missing lib.py line 2"

    # Verify context 1 coverage
    assert "tests/coverage/included_path/callee.py" in context1_covered
    assert expected_context_callee_runtime.issubset(context1_covered["tests/coverage/included_path/callee.py"])
    assert 2 in context1_covered["tests/coverage/included_path/lib.py"], "Context 1 missing lib.py line 2"
    assert (
        2 in context1_covered["tests/coverage/included_path/in_context_lib.py"]
    ), "Context 1 missing in_context_lib.py line 2"

    # Verify context 2 coverage
    assert "tests/coverage/included_path/callee.py" in context2_covered
    assert expected_context_callee_runtime.issubset(context2_covered["tests/coverage/included_path/callee.py"])
    assert (
        2 in context2_covered["tests/coverage/included_path/lib.py"]
    ), "Context 2 missing lib.py line 2 - re-instrumentation failed!"
    assert (
        2 in context2_covered["tests/coverage/included_path/in_context_lib.py"]
    ), "Context 2 missing in_context_lib.py line 2 - re-instrumentation failed!"

    # Critical: Both contexts should have the same runtime lines for callee.py
    context1_callee = context1_covered["tests/coverage/included_path/callee.py"]
    context2_callee = context2_covered["tests/coverage/included_path/callee.py"]

    assert expected_context_callee_runtime.issubset(context1_callee) and expected_context_callee_runtime.issubset(
        context2_callee
    ), f"Context coverages differ - re-instrumentation may have failed: context1={context1_callee}, context2={context2_callee}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess
def test_import_time_coverage_reinstrumentation():
    """
    Test that import-time coverage tracking re-instruments correctly between session collections.

    This ensures the re-instrumentation mechanism works for import-time dependencies
    across multiple start/stop coverage cycles.
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

    # First coverage collection
    ModuleCodeCollector.start_coverage()
    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    first_covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))  # type: ignore[union-attr]
    first_covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)  # type: ignore[union-attr]
    )

    # Clear the coverage to simulate a new test
    ModuleCodeCollector._instance.covered.clear()  # type: ignore[union-attr]

    # Second coverage collection - should get the same results
    ModuleCodeCollector.start_coverage()
    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    second_covered = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False)  # type: ignore[union-attr]
    )
    second_covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)  # type: ignore[union-attr]
    )

    # The key test is that import_time_callee.py is captured in BOTH collections
    # This verifies re-instrumentation is working (previously Context 2 would be empty)

    # Verify first collection captured the callee file
    assert (
        "tests/coverage/included_path/import_time_callee.py" in first_covered
    ), "First collection missing import_time_callee.py"
    first_callee = first_covered["tests/coverage/included_path/import_time_callee.py"]
    assert len(first_callee) > 0, "First collection has no lines for import_time_callee.py"

    # CRITICAL: Verify second collection also captured the callee file
    # This is the key test for re-instrumentation working
    # Before the fix, this would be empty or missing
    assert (
        "tests/coverage/included_path/import_time_callee.py" in second_covered
    ), "Second collection missing import_time_callee.py - re-instrumentation failed!"
    second_callee = second_covered["tests/coverage/included_path/import_time_callee.py"]
    assert (
        len(second_callee) > 0
    ), f"Second collection has no lines for import_time_callee.py - re-instrumentation failed! Got: {second_covered}"

    # Verify line 2 (the import statement that triggers execution) is in both
    # This is the minimum requirement to show re-instrumentation works
    assert 2 in first_callee, f"First collection missing line 2: {first_callee}"
    assert 2 in second_callee, f"Second collection missing line 2 - re-instrumentation failed! Got: {second_callee}"

    # With import tracking, verify both collections track dependencies
    assert "tests/coverage/included_path/import_time_callee.py" in first_covered_with_imports
    assert "tests/coverage/included_path/import_time_callee.py" in second_covered_with_imports


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

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

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

    # All contexts should have coverage for the module
    assert module_path in context1_covered, f"Context 1 missing {module_path}"
    assert module_path in context2_covered, f"Context 2 missing {module_path}"
    assert module_path in context3_covered, f"Context 3 missing {module_path}"

    if os.getenv("_DD_COVERAGE_FILE_LEVEL") == "true":
        # In file-level mode, we only verify the file was executed
        # All three contexts should have the file (line 0 sentinel)
        assert len(context1_covered[module_path]) > 0, "Context 1 has no coverage"
        assert len(context2_covered[module_path]) > 0, "Context 2 has no coverage - re-instrumentation failed!"
        assert len(context3_covered[module_path]) > 0, "Context 3 has no coverage - re-instrumentation failed!"
    else:
        # In line-level mode, verify specific lines
        # Expected lines for context 1 and 2 (same branch in function_with_branches)
        expected_lines_true_branch = {11, 12, 17, 18, 19, 20, 25, 26, 29, 34, 35, 36, 37, 38, 39}

        # Expected lines for context 3 (false branch in function_with_branches)
        expected_lines_false_branch = {11, 12, 17, 18, 19, 20, 25, 28, 29, 34, 35, 36, 37, 38, 39}

        # Verify contexts 1 and 2 captured the true branch
        assert (
            context1_covered[module_path] == expected_lines_true_branch
        ), f"Context 1 coverage mismatch: expected={expected_lines_true_branch} vs actual={context1_covered[module_path]}"

        assert (
            context2_covered[module_path] == expected_lines_true_branch
        ), f"Context 2 coverage mismatch: expected={expected_lines_true_branch} vs actual={context2_covered[module_path]}"

        # Verify context 3 captured the false branch
        assert (
            context3_covered[module_path] == expected_lines_false_branch
        ), f"Context 3 coverage mismatch: expected={expected_lines_false_branch} vs actual={context3_covered[module_path]}"

        # Critical assertions: Contexts 1 and 2 should have identical coverage
        # Context 3 should have the same number of lines (just different branch)
        assert len(context1_covered[module_path]) == len(
            context2_covered[module_path]
        ), f"Context 1 and 2 have different line counts: {len(context1_covered[module_path])} vs {len(context2_covered[module_path])}"

        assert len(context1_covered[module_path]) == len(
            context3_covered[module_path]
        ), f"Context 1 and 3 have different line counts: {len(context1_covered[module_path])} vs {len(context3_covered[module_path])}"
