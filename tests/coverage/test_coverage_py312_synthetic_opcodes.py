"""
Test coverage tracking for Python 3.12+ with opcodes not mapped to line numbers.

Python 3.12+ can generate opcodes with None line numbers in linestarts.
This happens for compiler-generated code like:
- Comprehensions
- Lambda functions
- Certain optimized bytecode patterns
- Decorator application
- Class definition helpers

Without proper None handling, this causes:
  TypeError: unsupported operand type(s) for //: 'NoneType' and 'int'

This test ensures we handle these cases gracefully by checking line is not None.
"""

import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(
    env={
        "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
        "DD_API_KEY": "foobar.baz",
    }
)
def test_coverage_with_synthetic_opcodes():
    """Test that coverage tracking handles synthetic opcodes with None line numbers."""
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    # Import a module that will exercise various code patterns that may generate synthetic opcodes
    from tests.coverage.included_path.synthetic_opcodes_module import test_comprehensions
    from tests.coverage.included_path.synthetic_opcodes_module import test_lambda
    from tests.coverage.included_path.synthetic_opcodes_module import test_nested_functions

    ModuleCodeCollector.start_coverage()

    # Execute code that generates synthetic opcodes
    result = test_comprehensions([1, 2, 3, 4, 5])
    assert result == [2, 4, 6, 8, 10]

    result = test_lambda(5)
    assert result == 25

    result = test_nested_functions(10)
    assert result == 40  # outer(10) -> inner(10) -> innermost(10) * 3 + 10 = 30 + 10 = 40

    ModuleCodeCollector.stop_coverage()

    # Verify we got coverage data without crashing
    lines = ModuleCodeCollector._instance.lines

    # Check that we tracked the module (path may vary, just check it exists)
    module_tracked = any("synthetic_opcodes_module.py" in path for path in lines.keys())
    assert module_tracked, f"Module not tracked. Found paths: {list(lines.keys())}"

    covered = ModuleCodeCollector._instance._get_covered_lines(include_imported=False)
    module_covered = any("synthetic_opcodes_module.py" in path for path in covered.keys())
    assert module_covered, f"Module not in covered. Found paths: {list(covered.keys())}"
    # The important thing is that we didn't crash with TypeError


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(
    env={
        "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
        "DD_API_KEY": "foobar.baz",
    }
)
def test_coverage_with_complex_expressions():
    """Test coverage with complex expressions that may have None line numbers."""
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    ModuleCodeCollector.start_coverage()

    # Create complex expressions inline that might generate synthetic opcodes
    # Dictionary comprehension
    result = {k: v * 2 for k, v in {"a": 1, "b": 2, "c": 3}.items()}
    assert result == {"a": 2, "b": 4, "c": 6}

    # Set comprehension
    result = {x * 2 for x in range(5)}
    assert result == {0, 2, 4, 6, 8}

    # Generator expression (consumed)
    result = list(x * 2 for x in range(5))
    assert result == [0, 2, 4, 6, 8]

    # Nested comprehension
    result = [[y * 2 for y in range(3)] for x in range(2)]
    assert result == [[0, 2, 4], [0, 2, 4]]

    # Lambda with complex expression
    func = lambda x: (x + 1) * (x + 2) if x > 0 else 0  # noqa: E731
    assert func(3) == 20

    ModuleCodeCollector.stop_coverage()

    # Verify we didn't crash
    covered = ModuleCodeCollector._instance._get_covered_lines(include_imported=False)
    assert isinstance(covered, dict)
    # If we got here without TypeError, we successfully handled None line numbers


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(
    env={
        "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
        "DD_API_KEY": "foobar.baz",
    }
)
def test_coverage_import_with_comprehensions():
    """
    Test that import-time comprehensions don't crash coverage.

    When a module has comprehensions at module level, Python 3.12+ may generate
    synthetic opcodes with None line numbers during import. This test ensures
    we handle that gracefully.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    # Install coverage BEFORE importing the module
    install(include_paths=[include_path])

    # This import will execute module-level code that may have None line numbers
    # If the bug exists, this will crash with TypeError
    from tests.coverage.included_path.synthetic_opcodes_module import MODULE_LEVEL_COMP

    # If we got here, we successfully handled None line numbers
    assert MODULE_LEVEL_COMP == [0, 3, 6, 9, 12]

    # Verify coverage was collected
    lines = ModuleCodeCollector._instance.lines
    module_tracked = any("synthetic_opcodes_module.py" in path for path in lines.keys())
    assert module_tracked, f"Module not tracked during import. Found paths: {list(lines.keys())}"
    # If we got here without TypeError, we successfully handled all import-time opcodes
