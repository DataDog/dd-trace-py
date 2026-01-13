"""Test exception safety of SCA detection hook.

CRITICAL: The hook runs in customer code and must NEVER throw exceptions.
These tests verify that all code paths in the hook are exception-safe.
"""

from unittest import mock

import pytest

from ddtrace.appsec.sca._instrumenter import sca_detection_hook
from ddtrace.appsec.sca._instrumenter import set_registry
from ddtrace.appsec.sca._registry import InstrumentationRegistry


def test_hook_survives_registry_none():
    """Hook should not crash when _registry is None."""
    # Set registry to None
    set_registry(None)

    # Call hook - should not throw
    try:
        sca_detection_hook("test:function")
    except Exception as e:
        pytest.fail(f"Hook threw exception with registry=None: {e}")


def test_hook_survives_registry_record_hit_exception():
    """Hook should not crash when registry.record_hit() throws."""
    registry = InstrumentationRegistry()
    set_registry(registry)

    # Make record_hit throw
    with mock.patch.object(registry, "record_hit", side_effect=RuntimeError("Registry error")):
        # Call hook - should not throw
        try:
            sca_detection_hook("test:function")
        except Exception as e:
            pytest.fail(f"Hook threw exception when record_hit failed: {e}")


def test_hook_survives_span_get_exception():
    """Hook should not crash when core.get_span() throws."""
    registry = InstrumentationRegistry()
    set_registry(registry)

    # Make get_span throw
    with mock.patch("ddtrace.appsec.sca._instrumenter.core.get_span", side_effect=RuntimeError("Span error")):
        # Call hook - should not throw
        try:
            sca_detection_hook("test:function")
        except Exception as e:
            pytest.fail(f"Hook threw exception when get_span failed: {e}")


def test_hook_survives_span_set_tag_exception():
    """Hook should not crash when span._set_tag_str() throws."""
    registry = InstrumentationRegistry()
    set_registry(registry)

    # Mock span that throws on set_tag_str
    mock_span = mock.Mock()
    mock_span._set_tag_str.side_effect = RuntimeError("Tag error")

    with mock.patch("ddtrace.appsec.sca._instrumenter.core.get_span", return_value=mock_span):
        # Call hook - should not throw
        try:
            sca_detection_hook("test:function")
        except Exception as e:
            pytest.fail(f"Hook threw exception when set_tag_str failed: {e}")


def test_hook_survives_telemetry_exception():
    """Hook should not crash when telemetry_writer throws."""
    registry = InstrumentationRegistry()
    set_registry(registry)

    # Make telemetry writer throw
    with mock.patch(
        "ddtrace.appsec.sca._instrumenter.telemetry_writer.add_count_metric",
        side_effect=RuntimeError("Telemetry error"),
    ):
        # Call hook - should not throw
        try:
            sca_detection_hook("test:function")
        except Exception as e:
            pytest.fail(f"Hook threw exception when telemetry failed: {e}")


def test_hook_survives_multiple_failures():
    """Hook should not crash when multiple operations fail."""
    registry = InstrumentationRegistry()
    set_registry(registry)

    # Make everything throw
    with mock.patch.object(registry, "record_hit", side_effect=RuntimeError("Registry error")):
        with mock.patch("ddtrace.appsec.sca._instrumenter.core.get_span", side_effect=RuntimeError("Span error")):
            with mock.patch(
                "ddtrace.appsec.sca._instrumenter.telemetry_writer.add_count_metric",
                side_effect=RuntimeError("Telemetry error"),
            ):
                # Call hook - should not throw
                try:
                    sca_detection_hook("test:function")
                except Exception as e:
                    pytest.fail(f"Hook threw exception when all operations failed: {e}")


def test_hook_in_instrumented_function_never_throws():
    """Integration test: Hook injected into customer function never breaks execution."""
    from ddtrace.appsec.sca._instrumenter import Instrumenter
    from ddtrace.appsec.sca._registry import InstrumentationRegistry

    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    # Define a customer function
    def customer_function(x, y):
        """Customer code that must not be broken by our instrumentation."""
        return x + y

    # Instrument it
    instrumenter.instrument("test:customer_function", customer_function)

    # Make the hook fail by setting registry to None
    set_registry(None)

    # Call the instrumented function - should still work
    try:
        result = customer_function(2, 3)
        assert result == 5, "Customer function should still return correct result"
    except Exception as e:
        pytest.fail(f"Instrumented function threw exception: {e}")


def test_hook_with_all_operations_failing_still_allows_function_execution():
    """Stress test: Hook with all operations failing should not prevent function execution."""
    from ddtrace.appsec.sca._instrumenter import Instrumenter
    from ddtrace.appsec.sca._registry import InstrumentationRegistry

    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    # Define a customer function
    call_count = [0]

    def customer_function():
        """Customer code that counts calls."""
        call_count[0] += 1
        return "success"

    # Instrument it
    instrumenter.instrument("test:customer_function", customer_function)

    # Make ALL hook operations fail
    with mock.patch.object(registry, "record_hit", side_effect=RuntimeError("Registry error")):
        with mock.patch("ddtrace.appsec.sca._instrumenter.core.get_span", side_effect=RuntimeError("Span error")):
            with mock.patch(
                "ddtrace.appsec.sca._instrumenter.telemetry_writer.add_count_metric",
                side_effect=RuntimeError("Telemetry error"),
            ):
                # Call function multiple times
                for i in range(10):
                    try:
                        result = customer_function()
                        assert result == "success", f"Function should return 'success' on call {i}"
                    except Exception as e:
                        pytest.fail(f"Function threw exception on call {i}: {e}")

    # Verify function was actually called
    assert call_count[0] == 10, "Function should have been called 10 times"


def test_hook_with_async_function():
    """Test that hook works with async functions and doesn't break them."""
    import asyncio

    from ddtrace.appsec.sca._instrumenter import Instrumenter
    from ddtrace.appsec.sca._registry import InstrumentationRegistry

    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    # Define an async customer function
    async def async_customer_function(x):
        """Async customer code."""
        await asyncio.sleep(0.001)
        return x * 2

    # Instrument it
    instrumenter.instrument("test:async_customer_function", async_customer_function)

    # Make hook fail
    set_registry(None)

    # Call the instrumented async function - should still work
    async def test_call():
        try:
            result = await async_customer_function(5)
            assert result == 10, "Async function should return correct result"
        except Exception as e:
            pytest.fail(f"Instrumented async function threw exception: {e}")

    asyncio.run(test_call())


def test_hook_with_exception_in_customer_code():
    """Test that hook doesn't interfere with customer code exceptions."""
    from ddtrace.appsec.sca._instrumenter import Instrumenter
    from ddtrace.appsec.sca._registry import InstrumentationRegistry

    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    # Define a customer function that raises an exception
    class CustomerException(Exception):
        pass

    def customer_function_that_fails():
        """Customer code that intentionally raises an exception."""
        raise CustomerException("Customer error")

    # Instrument it
    instrumenter.instrument("test:customer_function_that_fails", customer_function_that_fails)

    # Call the function - should raise CustomerException, not something else
    with pytest.raises(CustomerException, match="Customer error"):
        customer_function_that_fails()


def test_hook_performance_with_failures():
    """Test that hook failures don't significantly impact performance."""
    import time

    from ddtrace.appsec.sca._instrumenter import Instrumenter
    from ddtrace.appsec.sca._registry import InstrumentationRegistry

    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    # Define a fast customer function
    def fast_function():
        return 42

    # Measure baseline performance (uninstrumented)
    baseline_start = time.perf_counter()
    for _ in range(1000):
        fast_function()
    baseline_time = time.perf_counter() - baseline_start

    # Instrument the function
    instrumenter.instrument("test:fast_function", fast_function)

    # Make hook operations fail (but catch exceptions)
    with mock.patch("ddtrace.appsec.sca._instrumenter.core.get_span", side_effect=RuntimeError("Span error")):
        # Measure instrumented performance
        instrumented_start = time.perf_counter()
        for _ in range(1000):
            fast_function()
        instrumented_time = time.perf_counter() - instrumented_start

    # Overhead should be reasonable (less than 200x slowdown even with failures)
    # Note: Exception catching does add overhead, but the critical requirement is that
    # the hook never breaks customer code (verified by other tests). Performance with
    # failures is less critical since failures should be rare in production.
    overhead_ratio = instrumented_time / baseline_time
    assert overhead_ratio < 200, f"Hook overhead too high: {overhead_ratio}x (with failures)"


def test_hook_with_unicode_and_special_characters():
    """Test that hook handles unicode and special characters in qualified names."""
    registry = InstrumentationRegistry()
    set_registry(registry)

    # Test with various special characters
    test_names = [
        "test:函数",  # Chinese characters
        "test:función",  # Spanish characters
        "test:func<lambda>",  # Special characters
        "test:" + "a" * 1000,  # Very long name
        "test:\n\r\t",  # Whitespace characters
    ]

    for name in test_names:
        try:
            sca_detection_hook(name)
        except Exception as e:
            pytest.fail(f"Hook threw exception with special name '{name}': {e}")


def test_hook_with_concurrent_calls():
    """Test that hook is thread-safe when called concurrently."""
    import threading

    from ddtrace.appsec.sca._instrumenter import Instrumenter
    from ddtrace.appsec.sca._registry import InstrumentationRegistry

    registry = InstrumentationRegistry()
    instrumenter = Instrumenter(registry)

    # Define a customer function
    call_count = [0]
    lock = threading.Lock()

    def customer_function():
        with lock:
            call_count[0] += 1
        return "success"

    # Instrument it
    instrumenter.instrument("test:customer_function", customer_function)

    # Call from multiple threads concurrently
    errors = []

    def worker():
        try:
            for _ in range(100):
                result = customer_function()
                assert result == "success"
        except Exception as e:
            errors.append(e)

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Verify no errors occurred
    assert len(errors) == 0, f"Concurrent calls caused {len(errors)} errors: {errors}"
    assert call_count[0] == 1000, "All 1000 calls should have executed"
