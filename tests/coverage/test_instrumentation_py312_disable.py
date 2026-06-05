"""
Unit tests for Python 3.12+ coverage instrumentation.

Verifies:
- _event_handler returns sys.monitoring.DISABLE for performance
- register_coverage() / unregister_coverage() slot management
- instrument_all_lines() only instruments when ddtrace owns COVERAGE_ID
"""

import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_event_handler_returns_disable():
    """
    Test that _event_handler returns DISABLE after recording a line.

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


# ---------------------------------------------------------------------------
# Regression tests for unregister_coverage()
# Fix: release sys.monitoring.COVERAGE_ID so inline sub-sessions can use it
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_unregister_coverage_releases_coverage_id_when_held_by_datadog():
    """unregister_coverage() must free COVERAGE_ID when we hold it as 'datadog'."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import register_coverage
    from ddtrace.internal.coverage.instrumentation_py3_12 import unregister_coverage

    # Ensure we hold the slot as "datadog" (idempotent if already held)
    register_coverage()
    try:
        unregister_coverage()
        # Slot must now be free so another tool can claim it
        assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is None
    finally:
        # Ensure the slot is always freed even if the assertion fails
        try:
            sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)
        except Exception:
            pass


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_unregister_coverage_preserves_code_hooks():
    """unregister_coverage() must preserve _CODE_HOOKS for re-registration."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import _CODE_HOOKS
    from ddtrace.internal.coverage.instrumentation_py3_12 import register_coverage
    from ddtrace.internal.coverage.instrumentation_py3_12 import unregister_coverage

    register_coverage()
    code_obj = compile("z = 3", "<test>", "exec")
    _CODE_HOOKS[code_obj] = (lambda li: None, "/fake/path.py", {})
    try:
        unregister_coverage()
        # _CODE_HOOKS must be preserved so register_coverage() can re-enable events
        assert code_obj in _CODE_HOOKS
    finally:
        _CODE_HOOKS.pop(code_obj, None)
        try:
            sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)
        except Exception:
            pass


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_unregister_coverage_noop_when_slot_is_free():
    """unregister_coverage() must be a no-op when COVERAGE_ID is not registered."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import unregister_coverage

    # Ensure the slot is free before the test
    if sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is not None:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)

    # Should not raise
    unregister_coverage()
    assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_unregister_coverage_noop_when_held_by_other_tool():
    """unregister_coverage() must not steal COVERAGE_ID from a foreign tool."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import unregister_coverage

    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "other_tool")
    try:
        unregister_coverage()
        # Slot must still belong to the other tool
        assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) == "other_tool"
    finally:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_unregister_coverage_allows_subsequent_reclaim():
    """After unregister_coverage(), another tool can successfully claim COVERAGE_ID."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import unregister_coverage

    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "datadog")
    unregister_coverage()
    try:
        # This must not raise "tool 1 is already in use"
        sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "pytest-cov")
        assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) == "pytest-cov"
    finally:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)


def test_unregister_coverage_is_callable_on_all_python_versions():
    """instrumentation.py must export a callable unregister_coverage() on every supported version."""
    from ddtrace.internal.coverage.instrumentation import unregister_coverage

    # Must be callable without raising on any Python version
    unregister_coverage()


# ---------------------------------------------------------------------------
# Tests for register_coverage()
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_register_coverage_claims_slot():
    """register_coverage() must claim COVERAGE_ID as 'datadog'."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import register_coverage
    from ddtrace.internal.coverage.instrumentation_py3_12 import unregister_coverage

    # Ensure slot is free
    if sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is not None:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)

    try:
        result = register_coverage()
        assert result is True
        assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) == "datadog"
    finally:
        unregister_coverage()


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_register_coverage_returns_true_if_already_owned():
    """register_coverage() returns True without raising if we already own the slot."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import register_coverage
    from ddtrace.internal.coverage.instrumentation_py3_12 import unregister_coverage

    # Ensure slot is free
    if sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is not None:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)

    try:
        assert register_coverage() is True
        # Calling again should succeed (idempotent)
        assert register_coverage() is True
        assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) == "datadog"
    finally:
        unregister_coverage()


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_register_coverage_returns_false_when_slot_taken():
    """register_coverage() returns False when another tool holds COVERAGE_ID."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import register_coverage

    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "other_tool")
    try:
        result = register_coverage()
        assert result is False
        # Slot must still belong to the other tool
        assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) == "other_tool"
    finally:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)


def test_register_coverage_is_callable_on_all_python_versions():
    """instrumentation.py must export a callable register_coverage() on every supported version."""
    from ddtrace.internal.coverage.instrumentation import register_coverage

    # Must be callable without raising on any Python version
    register_coverage()


# ---------------------------------------------------------------------------
# Tests for instrument_all_lines() behavior
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_instrument_all_lines_skips_when_slot_held_by_foreign_tool():
    """instrument_all_lines() returns empty CoverageLines when another tool holds COVERAGE_ID."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines
    from ddtrace.internal.coverage.instrumentation_py3_12 import register_coverage
    from ddtrace.internal.coverage.instrumentation_py3_12 import unregister_coverage

    code_obj = compile("x = 1", "<test>", "exec")

    # The test suite may have registered ddtrace's coverage; release it first.
    unregister_coverage()
    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "coverage.py")
    try:
        _, lines = instrument_all_lines(code_obj, lambda li: None, "/fake.py", "pkg")
        assert len(lines) == 0
    finally:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)
        # Restore ddtrace's registration if it was active.
        register_coverage()


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_instrument_all_lines_skips_when_slot_is_free():
    """instrument_all_lines() returns empty when COVERAGE_ID is unclaimed.

    Registration is handled by installer.install() or the pytest plugins,
    not by instrument_all_lines() itself.
    """
    from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines

    # Ensure slot is free
    if sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is not None:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)

    code_obj = compile("y = 2", "<test>", "exec")
    _, lines = instrument_all_lines(code_obj, lambda li: None, "/fake.py", "pkg")
    assert len(lines) == 0
    # Slot must still be free — no lazy registration
    assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_instrument_all_lines_works_when_we_own_slot():
    """instrument_all_lines() instruments code when we own COVERAGE_ID."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines
    from ddtrace.internal.coverage.instrumentation_py3_12 import register_coverage
    from ddtrace.internal.coverage.instrumentation_py3_12 import unregister_coverage

    # Ensure slot is free, then register
    if sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is not None:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)

    try:
        register_coverage()
        code_obj = compile("z = 3\nw = 4", "<test>", "exec")
        _, lines = instrument_all_lines(code_obj, lambda li: None, "/fake.py", "pkg")
        # Should have instrumented some lines
        assert len(lines) > 0
    finally:
        unregister_coverage()
