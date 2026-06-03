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


# ---------------------------------------------------------------------------
# Regression tests for deregister_monitoring()
# Fix: release sys.monitoring.COVERAGE_ID so inline sub-sessions can use it
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_deregister_monitoring_releases_coverage_id_when_held_by_datadog():
    """deregister_monitoring() must free COVERAGE_ID when we hold it as 'datadog'."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import deregister_monitoring

    # Claim the slot as "datadog" (mirrors what instrument_all_lines does)
    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "datadog")
    try:
        deregister_monitoring()
        # Slot must now be free so another tool can claim it
        assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is None
    finally:
        # Ensure the slot is always freed even if the assertion fails
        try:
            sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)
        except Exception:
            pass


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_deregister_monitoring_clears_code_hooks():
    """deregister_monitoring() must empty _CODE_HOOKS so stale entries don't fire."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import _CODE_HOOKS
    from ddtrace.internal.coverage.instrumentation_py3_12 import deregister_monitoring

    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "datadog")
    code_obj = compile("z = 3", "<test>", "exec")
    _CODE_HOOKS[code_obj] = (lambda li: None, "/fake/path.py", {})
    try:
        deregister_monitoring()
        assert len(_CODE_HOOKS) == 0
    finally:
        _CODE_HOOKS.pop(code_obj, None)
        try:
            sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)
        except Exception:
            pass


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_deregister_monitoring_noop_when_slot_is_free():
    """deregister_monitoring() must be a no-op when COVERAGE_ID is not registered."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import deregister_monitoring

    # Ensure the slot is free before the test
    if sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is not None:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)

    # Should not raise
    deregister_monitoring()
    assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_deregister_monitoring_noop_when_held_by_other_tool():
    """deregister_monitoring() must not steal COVERAGE_ID from a foreign tool."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import deregister_monitoring

    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "other_tool")
    try:
        deregister_monitoring()
        # Slot must still belong to the other tool
        assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) == "other_tool"
    finally:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_deregister_monitoring_allows_subsequent_reclaim():
    """After deregister_monitoring(), another tool can successfully claim COVERAGE_ID."""
    from ddtrace.internal.coverage.instrumentation_py3_12 import deregister_monitoring

    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "datadog")
    deregister_monitoring()
    try:
        # This must not raise "tool 1 is already in use"
        sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "pytest-cov")
        assert sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) == "pytest-cov"
    finally:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)


def test_deregister_monitoring_is_callable_on_all_python_versions():
    """instrumentation.py must export a callable deregister_monitoring() on every supported version."""
    from ddtrace.internal.coverage.instrumentation import deregister_monitoring

    # Must be callable without raising on any Python version
    deregister_monitoring()


# ---------------------------------------------------------------------------
# Regression tests for the foreign-tool warning in instrument_all_lines()
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_instrument_all_lines_warns_once_when_slot_held_by_foreign_tool():
    """A single warning is emitted when COVERAGE_ID is held by a foreign tool, not once per call."""
    import ddtrace.internal.coverage.instrumentation_py3_12 as mod
    from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines

    code_obj = compile("x = 1", "<test>", "exec")

    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "coverage.py")
    mod._coverage_id_conflict_warned = False
    try:
        with pytest.MonkeyPatch().context() as mp:
            warnings = []
            mp.setattr(mod.log, "warning", lambda msg, *args: warnings.append(msg % args))

            # First call — must warn
            instrument_all_lines(code_obj, lambda li: None, "/fake.py", "pkg")
            assert len(warnings) == 1
            assert "coverage.py" in warnings[0]
            assert "ITR" in warnings[0]

            # Subsequent calls — must stay silent
            instrument_all_lines(code_obj, lambda li: None, "/fake.py", "pkg")
            instrument_all_lines(code_obj, lambda li: None, "/fake.py", "pkg")
            assert len(warnings) == 1
    finally:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)
        mod._coverage_id_conflict_warned = False


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_instrument_all_lines_no_warning_when_slot_is_free():
    """No warning is emitted when COVERAGE_ID is unclaimed — ddtrace registers normally."""
    import ddtrace.internal.coverage.instrumentation_py3_12 as mod
    from ddtrace.internal.coverage.instrumentation_py3_12 import deregister_monitoring
    from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines

    code_obj = compile("y = 2", "<test>", "exec")

    if sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is not None:
        sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)
    mod._coverage_id_conflict_warned = False
    try:
        with pytest.MonkeyPatch().context() as mp:
            warnings = []
            mp.setattr(mod.log, "warning", lambda msg, *args: warnings.append(msg % args))

            instrument_all_lines(code_obj, lambda li: None, "/fake.py", "pkg")
            assert warnings == []
    finally:
        deregister_monitoring()
        mod._coverage_id_conflict_warned = False


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring available on Python 3.12+")
def test_instrument_all_lines_warning_resets_after_deregister():
    """deregister_monitoring() resets the warned flag so a new session can warn again."""
    import ddtrace.internal.coverage.instrumentation_py3_12 as mod
    from ddtrace.internal.coverage.instrumentation_py3_12 import deregister_monitoring
    from ddtrace.internal.coverage.instrumentation_py3_12 import instrument_all_lines

    code_obj = compile("z = 3", "<test>", "exec")

    sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "coverage.py")
    mod._coverage_id_conflict_warned = False
    try:
        with pytest.MonkeyPatch().context() as mp:
            warnings = []
            mp.setattr(mod.log, "warning", lambda msg, *args: warnings.append(msg % args))

            # First session: one warning
            instrument_all_lines(code_obj, lambda li: None, "/fake.py", "pkg")
            assert len(warnings) == 1

            # Simulate end of session — deregister resets the flag
            sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)
            deregister_monitoring()  # resets _coverage_id_conflict_warned

            # Second session: foreign tool re-appears, must warn again
            sys.monitoring.use_tool_id(sys.monitoring.COVERAGE_ID, "coverage.py")
            instrument_all_lines(code_obj, lambda li: None, "/fake.py", "pkg")
            assert len(warnings) == 2
    finally:
        if sys.monitoring.get_tool(sys.monitoring.COVERAGE_ID) is not None:
            sys.monitoring.free_tool_id(sys.monitoring.COVERAGE_ID)
        mod._coverage_id_conflict_warned = False
