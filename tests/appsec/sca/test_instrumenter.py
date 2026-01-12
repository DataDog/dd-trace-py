"""Unit tests for Instrumenter and bytecode injection."""

import mock

from ddtrace.appsec.sca._instrumenter import Instrumenter
from ddtrace.appsec.sca._instrumenter import apply_instrumentation_updates
from ddtrace.appsec.sca._instrumenter import set_registry
from ddtrace.appsec.sca._registry import InstrumentationRegistry


def test_instrumenter_basic():
    """Test basic instrumentation of a function."""
    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    def test_func():
        return "test"

    # Instrument the function
    success = instrumenter.instrument("test_module:test_func", test_func)

    assert success is True
    assert registry.is_instrumented("test_module:test_func")


def test_instrumenter_already_instrumented():
    """Test that instrumenting an already-instrumented function returns True."""
    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    def test_func():
        return "test"

    # Instrument twice
    success1 = instrumenter.instrument("test_module:test_func", test_func)
    success2 = instrumenter.instrument("test_module:test_func", test_func)

    assert success1 is True
    assert success2 is True
    assert registry.is_instrumented("test_module:test_func")


def test_instrumenter_hook_called():
    """Test that the injected hook is actually called when function executes."""
    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    def test_func():
        return "test"

    # Instrument the function
    instrumenter.instrument("test_module:test_func", test_func)

    # Call the function
    result = test_func()

    # Hook should have recorded the hit
    assert result == "test"
    assert registry.get_hit_count("test_module:test_func") == 1


def test_instrumenter_hook_called_multiple_times():
    """Test that hook records multiple invocations."""
    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    def test_func(x):
        return x * 2

    # Instrument the function
    instrumenter.instrument("test_module:test_func", test_func)

    # Call the function multiple times
    for i in range(5):
        test_func(i)

    # Hook should have recorded all hits
    assert registry.get_hit_count("test_module:test_func") == 5


def test_instrumenter_preserves_function_behavior():
    """Test that instrumentation doesn't break function behavior."""
    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    def test_func(a, b, c=10):
        return a + b + c

    # Test original behavior
    original_result = test_func(1, 2, c=3)

    # Instrument
    instrumenter.instrument("test_module:test_func", test_func)

    # Test instrumented behavior
    instrumented_result = test_func(1, 2, c=3)

    # Should have same result
    assert original_result == instrumented_result == 6


def test_instrumenter_with_method():
    """Test instrumenting a class method."""
    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    class TestClass:
        def test_method(self):
            return "method"

    # Instrument the method
    success = instrumenter.instrument("test_module:TestClass.test_method", TestClass.test_method)

    assert success is True
    assert registry.is_instrumented("test_module:TestClass.test_method")

    # Call the method
    instance = TestClass()
    result = instance.test_method()

    assert result == "method"
    assert registry.get_hit_count("test_module:TestClass.test_method") == 1


def test_instrumenter_uninstrument_not_implemented():
    """Test that uninstrument returns False (not yet implemented)."""
    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    result = instrumenter.uninstrument("test_module:test_func")

    assert result is False


def test_set_registry():
    """Test setting global registry reference."""
    registry = InstrumentationRegistry()

    set_registry(registry)

    # Verify it's set by instrumenting a function and checking hit recording
    def test_func():
        pass

    instrumenter = Instrumenter(registry)
    instrumenter.instrument("test:func", test_func)
    test_func()

    assert registry.get_hit_count("test:func") == 1


def test_apply_instrumentation_updates_add():
    """Test apply_instrumentation_updates with targets to add."""
    # Clear any existing global registry state
    from ddtrace.appsec.sca._registry import _global_registry
    from ddtrace.appsec.sca._registry import _registry_lock

    with _registry_lock:
        if _global_registry:
            _global_registry.clear()

    # Test with a real module function
    targets_to_add = ["os.path:join"]
    targets_to_remove = []

    apply_instrumentation_updates(targets_to_add, targets_to_remove)

    # Verify target was added and instrumented
    from ddtrace.appsec.sca._registry import get_global_registry

    registry = get_global_registry()
    assert registry.has_target("os.path:join")
    assert registry.is_instrumented("os.path:join")


def test_apply_instrumentation_updates_add_nonexistent():
    """Test apply_instrumentation_updates with non-existent module (pending)."""
    # Clear any existing global registry state
    from ddtrace.appsec.sca._registry import _global_registry
    from ddtrace.appsec.sca._registry import _registry_lock

    with _registry_lock:
        if _global_registry:
            _global_registry.clear()

    targets_to_add = ["nonexistent.module:function"]
    targets_to_remove = []

    apply_instrumentation_updates(targets_to_add, targets_to_remove)

    # Verify target was added as pending
    from ddtrace.appsec.sca._registry import get_global_registry

    registry = get_global_registry()
    assert registry.has_target("nonexistent.module:function")
    assert registry.is_pending("nonexistent.module:function")
    assert not registry.is_instrumented("nonexistent.module:function")


def test_apply_instrumentation_updates_remove():
    """Test apply_instrumentation_updates with targets to remove."""
    # Clear and setup
    from ddtrace.appsec.sca._registry import _global_registry
    from ddtrace.appsec.sca._registry import _registry_lock
    from ddtrace.appsec.sca._registry import get_global_registry

    with _registry_lock:
        if _global_registry:
            _global_registry.clear()

    # First add a target
    targets_to_add = ["os.path:exists"]
    apply_instrumentation_updates(targets_to_add, [])

    registry = get_global_registry()
    assert registry.has_target("os.path:exists")

    # Now remove it
    targets_to_remove = ["os.path:exists"]
    apply_instrumentation_updates([], targets_to_remove)

    # Verify it was removed
    assert not registry.has_target("os.path:exists")


def test_apply_instrumentation_updates_already_tracked():
    """Test that already-tracked targets are skipped."""
    # Clear and setup
    from ddtrace.appsec.sca._registry import _global_registry
    from ddtrace.appsec.sca._registry import _registry_lock
    from ddtrace.appsec.sca._registry import get_global_registry

    with _registry_lock:
        if _global_registry:
            _global_registry.clear()

    targets = ["os.path:dirname"]

    # Add twice
    apply_instrumentation_updates(targets, [])
    apply_instrumentation_updates(targets, [])

    # Should only be added once
    registry = get_global_registry()
    assert registry.has_target("os.path:dirname")
    assert registry.is_instrumented("os.path:dirname")


def test_instrumenter_handles_injection_error():
    """Test that instrumentation handles inject_hook errors gracefully."""
    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    # Mock inject_hook to raise an error
    with mock.patch("ddtrace.appsec.sca._instrumenter.inject_hook") as mock_inject:
        mock_inject.side_effect = RuntimeError("Injection failed")

        def test_func():
            pass

        success = instrumenter.instrument("test:func", test_func)

        # Should return False and not crash
        assert success is False
        assert not registry.is_instrumented("test:func")


def test_apply_instrumentation_updates_mixed():
    """Test apply_instrumentation_updates with both adds and removes."""
    # Clear and setup
    from ddtrace.appsec.sca._registry import _global_registry
    from ddtrace.appsec.sca._registry import _registry_lock
    from ddtrace.appsec.sca._registry import get_global_registry

    with _registry_lock:
        if _global_registry:
            _global_registry.clear()

    # Add initial targets
    initial_targets = ["os.path:join", "os.path:exists"]
    apply_instrumentation_updates(initial_targets, [])

    registry = get_global_registry()
    assert registry.has_target("os.path:join")
    assert registry.has_target("os.path:exists")

    # Update: add one, remove one
    targets_to_add = ["os.path:dirname"]
    targets_to_remove = ["os.path:exists"]

    apply_instrumentation_updates(targets_to_add, targets_to_remove)

    # Verify state
    assert registry.has_target("os.path:join")  # Still there
    assert not registry.has_target("os.path:exists")  # Removed
    assert registry.has_target("os.path:dirname")  # Added
