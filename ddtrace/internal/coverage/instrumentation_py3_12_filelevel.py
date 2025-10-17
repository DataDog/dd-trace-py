"""
File-level coverage instrumentation for Python 3.12+ using PY_START events.

This is a high-performance alternative to line-level coverage that tracks which files
were executed rather than which specific lines. It uses PY_START events which fire
once per function call, making it much faster than LINE events which fire per line.

Performance characteristics:
- LINE events: O(lines × iterations)  
- PY_START events: O(functions × calls)

For a file with 100 lines and 5 functions called 10 times:
- LINE: 1,000 events (with DISABLE optimization)
- PY_START: 50 events (20x fewer!)
"""

import sys
from types import CodeType
import typing as t

from ddtrace.internal.bytecode_injection import HookType
from ddtrace.internal.logger import get_logger
from ddtrace.internal.test_visibility.coverage_lines import CoverageLines


log = get_logger(__name__)

# This is primarily to make mypy happy without having to nest the rest of this module behind a version check
assert sys.version_info >= (3, 12)  # nosec

# Store: (hook, path) - We only need the file path, not line-by-line details
_CODE_HOOKS: t.Dict[CodeType, t.Tuple[HookType, str]] = {}

# Track all instrumented code objects so we can re-enable monitoring between tests/suites
_DEINSTRUMENTED_CODE_OBJECTS: t.Set[CodeType] = set()


def instrument_for_file_coverage(code: CodeType, hook: HookType, path: str, package: str) -> t.Tuple[CodeType, CoverageLines]:
    """
    Instrument code for file-level coverage tracking using Python 3.12's monitoring API.
    
    This uses PY_START events which fire when a function starts executing, making it
    much more efficient than line-level coverage for scenarios where you only need
    to know which files were executed.

    Args:
        code: The code object to instrument
        hook: The hook function to call when the file is executed
        path: The file path
        package: The package name (unused for file-level, but kept for API compatibility)

    Returns:
        Tuple of (code object, empty CoverageLines since we don't track individual lines)
        
    Note: The hook will be called with (None, path, None) to indicate file-level coverage
    """
    coverage_tool = sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID)
    if coverage_tool is not None and coverage_tool != "datadog":
        log.debug("Coverage tool '%s' already registered, not gathering coverage", coverage_tool)
        return code, CoverageLines()

    if coverage_tool is None:
        log.debug("Registering file-level coverage tool")
        _register_monitoring()

    return _instrument_with_py_start(code, hook, path, package)


def _py_start_event_handler(code: CodeType, instruction_offset: int) -> t.Any:
    """
    Callback for PY_START events.
    
    This fires once when a function starts executing. We use this to detect that
    the file containing this code object was executed.
    """
    hook_data = _CODE_HOOKS.get(code)
    if hook_data is None:
        # Track this code object so we can re-enable monitoring for it later
        _DEINSTRUMENTED_CODE_OBJECTS.add(code)
        return sys.monitoring.DISABLE

    hook, path = hook_data

    # Report file-level coverage using line 0 as a sentinel value
    # Line 0 indicates "file was executed" without specific line information
    # The hook signature expects (line, path, import_name) so we pass (0, path, None)
    hook((0, path, None))

    # Track this code object so we can re-enable monitoring for it later
    _DEINSTRUMENTED_CODE_OBJECTS.add(code)
    
    # Return DISABLE to prevent future callbacks for this function
    # This means each function is only reported once per context
    return sys.monitoring.DISABLE


def _register_monitoring():
    """
    Register the file-level coverage tool with the monitoring system.
    """
    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "datadog")

    # Register the PY_START callback (much cheaper than LINE)
    sys.monitoring.register_callback(
        sys.monitoring.COVERAGE_ID, sys.monitoring.events.PY_START, _py_start_event_handler
    )


def reset_monitoring_for_new_context():
    """
    Re-enable monitoring for all instrumented code objects.

    This should be called when starting a new coverage context (e.g., per-test or per-suite).
    It re-enables monitoring that was disabled by previous DISABLE returns.
    """
    # restart_events() re-enables all events that were disabled by returning DISABLE
    # This resets the per-function disable state across all code objects
    sys.monitoring.restart_events()
    
    # Then re-enable local events for all instrumented code objects
    # This ensures monitoring is active for the new context
    for code in _DEINSTRUMENTED_CODE_OBJECTS:
        # Use PY_START event instead of LINE for file-level coverage
        sys.monitoring.set_local_events(sys.monitoring.COVERAGE_ID, code, sys.monitoring.events.PY_START)


def _instrument_with_py_start(
    code: CodeType, hook: HookType, path: str, package: str
) -> t.Tuple[CodeType, CoverageLines]:
    """
    Enable PY_START events for the code object and all nested code objects.
    
    This recursively instruments all functions in the module so that any function
    execution will trigger the file-level coverage callback.
    """
    # Enable local PY_START events for this code object
    sys.monitoring.set_local_events(sys.monitoring.COVERAGE_ID, code, sys.monitoring.events.PY_START)

    # Register the hook for this code object
    _CODE_HOOKS[code] = (hook, path)

    # Recursively instrument nested code objects (functions, classes, etc.)
    for nested_code in (_ for _ in code.co_consts if isinstance(_, CodeType)):
        _, _ = instrument_for_file_coverage(nested_code, hook, path, package)

    # Return CoverageLines with line 0 as sentinel to indicate file-level coverage
    # Line 0 means "file was instrumented/executed" without specific line details
    lines = CoverageLines()
    lines.add(0)
    return code, lines


# Comparison of approaches:
#
# LINE events (current):
# - Pros: Precise line-by-line coverage, detailed information
# - Cons: Expensive (fires once per line execution), high overhead in loops
# - Use case: When you need to know exactly which lines were executed
#
# PY_START events (this file):
# - Pros: Much faster (fires once per function call), low overhead
# - Cons: Only file-level granularity, can't distinguish which parts of file
# - Use case: When you only need to know which files were executed (e.g., file-level test selection)
#
# Performance example:
# File: 100 lines, 5 functions, function called 10 times in a loop
# - LINE events: ~100 events per iteration = 1,000 total (with DISABLE)
# - PY_START events: 5 functions × 10 calls = 50 total (20x improvement!)

