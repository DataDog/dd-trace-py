"""
Unit test for Python 3.12+ instrumentation DISABLE optimization.

Verifies that _line_event_handler returns sys.monitoring.DISABLE to prevent
repeated callbacks for the same line within a context.
"""

import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_event_handler_returns_disable():
    """
    Test that _line_event_handler returns DISABLE after recording a line.

    This is critical for performance - returning DISABLE prevents the monitoring
    system from calling the handler repeatedly for the same line (e.g., in loops).
    """
    from ddtrace.internal.coverage.instrumentation_py3_12 import _CODE_HOOKS
    from ddtrace.internal.coverage.instrumentation_py3_12 import _event_handler

    # Create a simple code object and register it
    code_obj = compile("x = 1", "<test>", "exec")

    # Track calls to the hook
    calls = []

    def mock_hook(line_info):
        calls.append(line_info)

    # Register the code object with our hook
    _CODE_HOOKS[code_obj] = (mock_hook, "/test/path.py", {})

    try:
        # Call the handler
        result = _event_handler(code_obj, 1)

        # CRITICAL: Must return DISABLE to prevent repeated callbacks
        assert result == sys.monitoring.DISABLE, f"_line_event_handler must return sys.monitoring.DISABLE, got {result}"

        # Verify the hook was called
        assert len(calls) == 1
        assert calls[0] == (1, "/test/path.py", None)
    finally:
        # Cleanup
        if code_obj in _CODE_HOOKS:
            del _CODE_HOOKS[code_obj]


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_event_handler_returns_disable_for_missing_code():
    """Test that handler returns DISABLE even when code object is missing (graceful error handling)."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import _event_handler

    # Create a code object that's NOT registered
    code_obj = compile("y = 2", "<test>", "exec")

    # Call handler with unregistered code object
    result = _event_handler(code_obj, 1)

    # Should still return DISABLE (graceful handling)
    assert result == sys.monitoring.DISABLE, f"Handler should return DISABLE even for missing code, got {result}"
