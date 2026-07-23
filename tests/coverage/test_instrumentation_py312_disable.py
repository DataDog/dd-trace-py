"""
Unit test for Python 3.12+ instrumentation DISABLE optimization.

Verifies that _line_event_handler returns sys.monitoring.DISABLE to prevent
repeated callbacks for the same line within a context.
"""

import sys

import pytest


# AIDEV-NOTE: Tests for the "Datadog is the only monitoring tool" branch run in-process
# and skip when a session-level tool such as pytest-cov already owns another slot. The
# neighboring tests explicitly register another tool to cover the opposite branch.


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_event_handler_returns_disable():
    """
    Test that _line_event_handler returns DISABLE after recording a line.

    This is critical for performance - returning DISABLE prevents the monitoring
    system from calling the handler repeatedly for the same line (e.g., in loops).
    """
    import ddtrace.internal.coverage.instrumentation_py3_12 as m
    from ddtrace.internal.coverage.instrumentation_py3_12 import _CODE_HOOKS
    from ddtrace.internal.coverage.instrumentation_py3_12 import _event_handler

    # Create a simple code object and register it
    code_obj = compile("x = 1", "<test>", "exec")

    # Track calls to the hook
    calls = []

    def mock_hook(line_info):
        calls.append(line_info)

    # Register the code object with our hook
    _CODE_HOOKS[code_obj] = (mock_hook, "/test/path.py", {}, None, None, None)

    # Pin the DISABLE optimisation on: this test is specifically about the DISABLE-returning
    # behavior, not about how the flag gets computed. Outside this test, the real coverage
    # collection path (CollectInContext.__enter__) may have already flipped it to False -
    # e.g. when another sys.monitoring tool such as pytest-cov's coverage.py (which defaults
    # to the sys.monitoring "sysmon" core on Python 3.14+) is registered for this session.
    prev_use_disable_optimization = m._use_disable_optimization
    m._use_disable_optimization = True

    try:
        # Call the handler
        result = _event_handler(code_obj, 1)

        # CRITICAL: Must return DISABLE to prevent repeated callbacks
        assert result == sys.monitoring.DISABLE, f"_line_event_handler must return sys.monitoring.DISABLE, got {result}"

        # Verify the hook was called
        assert len(calls) == 1
        assert calls[0] == (1, "/test/path.py", None)
    finally:
        m._use_disable_optimization = prev_use_disable_optimization
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


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_ensure_registered_claims_a_candidate_slot():
    """Test that _ensure_registered() claims a slot from _DD_CANDIDATE_SLOTS (4, 3, 1)."""
    import sys

    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    # Free any slot we previously acquired
    if m._DD_TOOL_ID is not None and sys.monitoring.get_tool(m._DD_TOOL_ID) == "datadog":
        sys.monitoring.free_tool_id(m._DD_TOOL_ID)
    m._DD_TOOL_ID = None

    result = m._ensure_registered()

    try:
        assert result is True, "_ensure_registered() must return True on success"
        assert m._DD_TOOL_ID is not None, "_ensure_registered() must set _DD_TOOL_ID"
        assert m._DD_TOOL_ID in m._DD_CANDIDATE_SLOTS, (
            f"Acquired slot {m._DD_TOOL_ID} is not in candidate slots {m._DD_CANDIDATE_SLOTS}"
        )
        assert sys.monitoring.get_tool(m._DD_TOOL_ID) == "datadog", (
            f"Expected 'datadog' at slot {m._DD_TOOL_ID}, got {sys.monitoring.get_tool(m._DD_TOOL_ID)}"
        )
    finally:
        if m._DD_TOOL_ID is not None and sys.monitoring.get_tool(m._DD_TOOL_ID) == "datadog":
            sys.monitoring.free_tool_id(m._DD_TOOL_ID)
        m._DD_TOOL_ID = None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_has_other_monitoring_tools_false_when_alone():
    """has_other_monitoring_tools() returns False when only datadog is registered."""
    import sys

    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    # Ensure we have a registered slot
    if m._DD_TOOL_ID is None or sys.monitoring.get_tool(m._DD_TOOL_ID) != "datadog":
        m._DD_TOOL_ID = None
        m._ensure_registered()

    try:
        if m.has_other_monitoring_tools():
            pytest.skip("requires Datadog to be the only active sys.monitoring tool")
        assert m.has_other_monitoring_tools() is False
    finally:
        if m._DD_TOOL_ID is not None and sys.monitoring.get_tool(m._DD_TOOL_ID) == "datadog":
            sys.monitoring.free_tool_id(m._DD_TOOL_ID)
        m._DD_TOOL_ID = None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_has_other_monitoring_tools_true_when_other_tool_present():
    """has_other_monitoring_tools() returns True when another tool occupies a slot."""
    import sys

    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    # Ensure we have a registered slot
    if m._DD_TOOL_ID is None or sys.monitoring.get_tool(m._DD_TOOL_ID) != "datadog":
        m._DD_TOOL_ID = None
        m._ensure_registered()

    # Pick a slot that is NOT ours
    other_slot = next(s for s in range(6) if s != m._DD_TOOL_ID and not sys.monitoring.get_tool(s))
    sys.monitoring.use_tool_id(other_slot, "other_tool")

    try:
        assert m.has_other_monitoring_tools() is True
    finally:
        sys.monitoring.free_tool_id(other_slot)
        if m._DD_TOOL_ID is not None and sys.monitoring.get_tool(m._DD_TOOL_ID) == "datadog":
            sys.monitoring.free_tool_id(m._DD_TOOL_ID)
        m._DD_TOOL_ID = None


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_update_disable_optimization_disables_when_other_tool_present():
    """update_disable_optimization() sets the flag to False when another tool is active."""
    import sys

    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    # Ensure we have a registered slot
    if m._DD_TOOL_ID is None or sys.monitoring.get_tool(m._DD_TOOL_ID) != "datadog":
        m._DD_TOOL_ID = None
        m._ensure_registered()

    # Pick a free slot and register another tool
    other_slot = next(s for s in range(6) if s != m._DD_TOOL_ID and not sys.monitoring.get_tool(s))
    sys.monitoring.use_tool_id(other_slot, "other_tool")

    try:
        result = m.update_disable_optimization()
        assert result is False, "Should disable optimization when another tool is present"
        assert m._use_disable_optimization is False
    finally:
        sys.monitoring.free_tool_id(other_slot)
        if m._DD_TOOL_ID is not None and sys.monitoring.get_tool(m._DD_TOOL_ID) == "datadog":
            sys.monitoring.free_tool_id(m._DD_TOOL_ID)
        m._DD_TOOL_ID = None
        m._use_disable_optimization = True


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_update_disable_optimization_enables_when_alone():
    """update_disable_optimization() sets the flag to True when only datadog is registered."""
    import sys

    import ddtrace.internal.coverage.instrumentation_py3_12 as m

    # Ensure we have a registered slot
    if m._DD_TOOL_ID is None or sys.monitoring.get_tool(m._DD_TOOL_ID) != "datadog":
        m._DD_TOOL_ID = None
        m._ensure_registered()

    try:
        if m.has_other_monitoring_tools():
            pytest.skip("requires Datadog to be the only active sys.monitoring tool")
        # Force to False first, then verify it gets set back to True
        m._use_disable_optimization = False
        result = m.update_disable_optimization()
        assert result is True, "Should enable optimization when no other tool is present"
        assert m._use_disable_optimization is True
    finally:
        if m._DD_TOOL_ID is not None and sys.monitoring.get_tool(m._DD_TOOL_ID) == "datadog":
            sys.monitoring.free_tool_id(m._DD_TOOL_ID)
        m._DD_TOOL_ID = None
        m._use_disable_optimization = True


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_update_disable_optimization_rearmed_on_transition():
    """On True→False transition, update_disable_optimization() calls _rearm_all_events()
    which re-enables events for our tool via a per-code-object set_local_events() toggle
    (tool-scoped, does not touch any other registered tool's disabled-event state).
    """
    import sys

    import ddtrace.internal.coverage.instrumentation_py3_12 as m
    from ddtrace.internal.coverage.instrumentation_py3_12 import _CODE_HOOKS

    # Ensure we have a registered slot
    if m._DD_TOOL_ID is None or sys.monitoring.get_tool(m._DD_TOOL_ID) != "datadog":
        m._DD_TOOL_ID = None
        m._ensure_registered()

    # Set up a real code object and register it in _CODE_HOOKS
    code_obj = compile("x = 1", "<test_rearm>", "exec")
    calls: list[object] = []
    _CODE_HOOKS[code_obj] = (lambda info: calls.append(info), "/test/rearm.py", {}, None, None, None)
    sys.monitoring.set_local_events(m._DD_TOOL_ID, code_obj, m.EVENT)

    # Start in DISABLE mode (default)
    m._use_disable_optimization = True

    # Simulate: _event_handler returned DISABLE for this code object, silencing events.
    # Confirm events fire, return DISABLE, and then stop firing (event silenced).
    result = m._event_handler(code_obj, 1)
    assert result == sys.monitoring.DISABLE

    # Now another tool registers (simulating coverage.py)
    other_slot = next(s for s in range(6) if s != m._DD_TOOL_ID and not sys.monitoring.get_tool(s))
    sys.monitoring.use_tool_id(other_slot, "other_tool")

    try:
        # Transition: True→False should call _rearm_all_events() internally
        new_val = m.update_disable_optimization()
        assert new_val is False

        # After re-arming, _event_handler should fire again for our code object
        calls.clear()
        result2 = m._event_handler(code_obj, 1)
        assert result2 is None, "After re-arm, handler must return None (not DISABLE)"
        assert len(calls) == 1, "Handler must fire after re-arming"
    finally:
        sys.monitoring.free_tool_id(other_slot)
        if code_obj in _CODE_HOOKS:
            del _CODE_HOOKS[code_obj]
        if m._DD_TOOL_ID is not None and sys.monitoring.get_tool(m._DD_TOOL_ID) == "datadog":
            sys.monitoring.free_tool_id(m._DD_TOOL_ID)
        m._DD_TOOL_ID = None
        m._use_disable_optimization = True


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Python 3.12+ monitoring API only")
def test_event_handler_returns_none_when_other_tool_present():
    """When another sys.monitoring tool is active, _event_handler returns None instead of DISABLE."""
    import sys

    import ddtrace.internal.coverage.instrumentation_py3_12 as m
    from ddtrace.internal.coverage.instrumentation_py3_12 import _CODE_HOOKS
    from ddtrace.internal.coverage.instrumentation_py3_12 import _event_handler

    # Ensure we have a registered slot
    if m._DD_TOOL_ID is None or sys.monitoring.get_tool(m._DD_TOOL_ID) != "datadog":
        m._DD_TOOL_ID = None
        m._ensure_registered()

    # Register another tool
    other_slot = next(s for s in range(6) if s != m._DD_TOOL_ID and not sys.monitoring.get_tool(s))
    sys.monitoring.use_tool_id(other_slot, "other_tool")

    # Set up a code hook
    code_obj = compile("x = 1", "<test>", "exec")
    calls = []
    _CODE_HOOKS[code_obj] = (lambda info: calls.append(info), "/test/path.py", {}, None, None, None)

    try:
        # Update the flag based on detected tools
        m.update_disable_optimization()

        result = _event_handler(code_obj, 1)
        assert result is None, f"Should return None when another tool is present, got {result}"
        assert len(calls) == 1
    finally:
        sys.monitoring.free_tool_id(other_slot)
        if code_obj in _CODE_HOOKS:
            del _CODE_HOOKS[code_obj]
        if m._DD_TOOL_ID is not None and sys.monitoring.get_tool(m._DD_TOOL_ID) == "datadog":
            sys.monitoring.free_tool_id(m._DD_TOOL_ID)
        m._DD_TOOL_ID = None
        m._use_disable_optimization = True
