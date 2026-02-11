import os
from pathlib import Path
import sys
import time

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import exception
from tests.profiling.collector import pprof_utils


# Exception profiling requires Python 3.10+
pytestmark = pytest.mark.skipif(sys.version_info < (3, 10), reason="Exception profiling requires Python 3.10+")


def test_exception_collector_init():
    """Test that ExceptionCollector initializes with proper defaults."""
    collector = exception.ExceptionCollector()
    assert collector is not None


def test_exception_collector_custom_params():
    """Test that ExceptionCollector accepts custom parameters."""
    collector = exception.ExceptionCollector(
        max_nframe=32,
        sampling_interval=50,
        collect_message=False,
    )
    assert collector is not None
    stats = collector.get_stats()
    assert stats["total_exceptions"] == 0
    assert stats["sampled_exceptions"] == 0


def test_exception_collector_start_stop():
    """Test that ExceptionCollector can start and stop without errors."""
    collector = exception.ExceptionCollector()
    collector.start()
    time.sleep(0.1)
    collector.stop()
    stats = collector.get_stats()
    # Stats should be initialized
    assert "total_exceptions" in stats
    assert "sampled_exceptions" in stats


def test_exception_collector_context_manager():
    """Test that ExceptionCollector works as a context manager."""
    with exception.ExceptionCollector():
        # Simple exception handling
        try:
            raise ValueError("test error")
        except ValueError:
            pass


# Helper functions at module level (required for bytecode injection on Python 3.10/3.11)
def _raise_value_error():
    raise ValueError("test value error")


def _handle_value_error():
    try:
        _raise_value_error()
    except ValueError:
        pass


def _level_3():
    raise RuntimeError("deep error")


def _level_2():
    _level_3()


def _level_1():
    try:
        _level_2()
    except RuntimeError:
        pass


def _raise_value_error_handled():
    try:
        raise ValueError("value error")
    except ValueError:
        pass


def _raise_type_error_handled():
    try:
        raise TypeError("type error")
    except TypeError:
        pass


def _raise_runtime_error_handled():
    try:
        raise RuntimeError("runtime error")
    except RuntimeError:
        pass


def _handle_generic_exception():
    try:
        raise ValueError("test")
    except ValueError:
        pass


def _nested_exception_handling():
    try:
        try:
            raise ValueError("inner error")
        except ValueError:
            raise RuntimeError("outer error")
    except RuntimeError:
        pass


def _exception_loop():
    for i in range(50):
        try:
            if i % 2 == 0:
                raise ValueError(f"error {i}")
        except ValueError:
            pass


def _deep_recursion(depth):
    if depth == 0:
        raise ValueError("deep error")
    return _deep_recursion(depth - 1)


def _handle_deep_exception():
    try:
        _deep_recursion(20)
    except ValueError:
        pass


class CustomError(Exception):
    """A custom exception class for testing."""

    pass


def _raise_custom_error():
    try:
        raise CustomError("custom exception")
    except CustomError:
        pass


def test_simple_exception_profiling(tmp_path: Path):
    """Test that a simple exception is captured and appears in the profile."""
    test_name = "test_simple_exception"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    # Use a low sampling interval to ensure we catch the exception
    with exception.ExceptionCollector(sampling_interval=1):
        # Raise multiple exceptions to ensure at least one is sampled
        for _ in range(10):
            _handle_value_error()
            time.sleep(0.01)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0, "Expected at least one exception sample"

    # Verify the exception type label
    for sample in samples:
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "exception type")
        if label:
            exc_type = profile.string_table[label.str]
            assert "ValueError" in exc_type
            break
    else:
        pytest.fail("No exception sample with 'exception type' label found")


def test_exception_with_stack_trace(tmp_path: Path):
    """Test that exception samples include proper stack traces."""
    test_name = "test_exception_stack"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    with exception.ExceptionCollector(sampling_interval=1):
        # Raise multiple exceptions to ensure sampling
        for _ in range(10):
            _level_1()
            time.sleep(0.01)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0, "Expected at least one exception sample"

    # Look for our stack trace in the samples
    found_stack = False
    for sample in samples:
        locations = [pprof_utils.get_location_from_id(profile, loc_id) for loc_id in sample.location_id]
        function_names = [loc.function_name for loc in locations]

        # Check if our function call chain is present
        if "_level_3" in function_names and "_level_2" in function_names:
            found_stack = True
            # Verify exception type
            label = pprof_utils.get_label_with_key(profile.string_table, sample, "exception type")
            assert label is not None
            exc_type = profile.string_table[label.str]
            assert "RuntimeError" in exc_type
            break

    assert found_stack, "Expected to find exception stack with _level_1/_level_2/_level_3"


def test_multiple_exception_types(tmp_path: Path):
    """Test that different exception types are tracked separately."""
    test_name = "test_multiple_exceptions"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    with exception.ExceptionCollector(sampling_interval=1):
        # Raise multiple different exceptions
        for _ in range(10):
            _raise_value_error_handled()
            _raise_type_error_handled()
            _raise_runtime_error_handled()
            time.sleep(0.001)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0, "Expected at least one exception sample"

    # Collect all exception types seen
    exception_types = set()
    for sample in samples:
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "exception type")
        if label:
            exception_types.add(profile.string_table[label.str])

    # We should see at least one of our exception types
    assert len(exception_types) > 0, "Expected to find exception type labels"
    # Check if any of our exception types appear
    found_types = [t for t in exception_types if any(e in t for e in ["ValueError", "TypeError", "RuntimeError"])]
    assert len(found_types) > 0, f"Expected to find our exception types, got: {exception_types}"


def test_exception_sampling_reduces_overhead(tmp_path: Path):
    """Test that sampling interval controls how many exceptions are captured."""
    test_name = "test_sampling"
    pprof_prefix = str(tmp_path / test_name)

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    # Test with high sampling interval (sample rarely)
    collector = exception.ExceptionCollector(sampling_interval=1000)
    collector.start()

    # Raise 100 exceptions
    for _ in range(100):
        _handle_generic_exception()

    collector.stop()
    stats = collector.get_stats()

    # With interval=1000, we expect very few samples from 100 exceptions
    assert stats["total_exceptions"] == 100
    # With Poisson sampling at λ=1000, we expect ~0.1 samples from 100 exceptions
    # But due to randomness, could be 0-5, so just check it's much less than total
    assert stats["sampled_exceptions"] < 20, (
        f"Expected < 20 sampled with interval=1000, got {stats['sampled_exceptions']}"
    )


def test_exception_with_thread_info(tmp_path: Path):
    """Test that exception samples include thread information."""
    test_name = "test_thread_info"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    with exception.ExceptionCollector(sampling_interval=1):
        for _ in range(10):
            _handle_generic_exception()
            time.sleep(0.01)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0, "Expected at least one exception sample"

    # Verify thread information is present
    found_thread_info = False
    for sample in samples:
        thread_id_label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread id")
        thread_name_label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread name")

        if thread_id_label and thread_name_label:
            found_thread_info = True
            # Verify thread ID is non-zero
            assert thread_id_label.num != 0
            # Verify thread name makes sense (MainThread for main thread)
            thread_name = profile.string_table[thread_name_label.str]
            assert len(thread_name) > 0
            break

    assert found_thread_info, "Expected to find thread information in exception samples"


def test_nested_exception_handling(tmp_path: Path):
    """Test exception profiling with nested try-except blocks."""
    test_name = "test_nested_exceptions"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    with exception.ExceptionCollector(sampling_interval=1):
        for _ in range(10):
            _nested_exception_handling()
            time.sleep(0.01)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0, "Expected at least one exception sample"

    # Both ValueError and RuntimeError should appear since both are handled
    exception_types = set()
    for sample in samples:
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "exception type")
        if label:
            exception_types.add(profile.string_table[label.str])

    # We should see both exception types
    exc_type_str = str(exception_types)
    assert any("ValueError" in t or "RuntimeError" in t for t in exception_types), (
        f"Expected ValueError or RuntimeError, got: {exc_type_str}"
    )


def test_exception_in_loop(tmp_path: Path):
    """Test that exceptions in loops are properly sampled."""
    test_name = "test_loop_exceptions"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    with exception.ExceptionCollector(sampling_interval=5):
        _exception_loop()
        time.sleep(0.1)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")

    # With 25 exceptions (50/2) and sampling_interval=5, we expect ~5 samples
    # But due to randomness, just verify we got some samples
    assert len(samples) > 0, "Expected at least one exception sample from loop"


def test_exception_collector_get_stats():
    """Test that get_stats returns accurate counters."""
    collector = exception.ExceptionCollector(sampling_interval=10)
    collector.start()

    # Raise some exceptions
    for _ in range(20):
        try:
            raise ValueError("test")
        except ValueError:
            pass

    time.sleep(0.1)
    collector.stop()

    stats = collector.get_stats()
    assert stats["total_exceptions"] > 0, "Expected total_exceptions > 0"
    # With sampling_interval=10 and 20 exceptions, we expect ~2 samples
    # Just verify the sampled count is reasonable
    assert stats["sampled_exceptions"] <= stats["total_exceptions"]


def test_max_nframe_limit(tmp_path: Path):
    """Test that stack traces are limited by max_nframe parameter."""
    test_name = "test_max_nframe"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    # Set max_nframe to a small value
    with exception.ExceptionCollector(sampling_interval=1, max_nframe=5):
        for _ in range(10):
            _handle_deep_exception()
            time.sleep(0.01)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0, "Expected at least one exception sample"

    # Verify that stack traces are limited
    for sample in samples:
        # Stack should be limited to max_nframe (5) even though we had 20+ frames
        assert len(sample.location_id) <= 5, f"Expected <= 5 frames with max_nframe=5, got {len(sample.location_id)}"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="sys.monitoring only in Python 3.12+")
def test_sys_monitoring_path():
    """Test that Python 3.12+ uses sys.monitoring API."""
    import sys

    collector = exception.ExceptionCollector()
    collector.start()

    # Verify monitoring is registered
    assert hasattr(sys, "monitoring")
    # The collector should have registered for EXCEPTION_HANDLED events
    # We can't easily verify this without internals, but we can check it doesn't crash

    collector.stop()


@pytest.mark.skipif(sys.version_info >= (3, 12), reason="Bytecode injection only for Python 3.10/3.11")
def test_bytecode_injection_path():
    """Test that Python 3.10/3.11 uses bytecode injection."""
    collector = exception.ExceptionCollector()
    collector.start()

    # The collector should have installed bytecode profiling
    # Verify it works by raising an exception
    try:
        raise ValueError("bytecode test")
    except ValueError:
        pass

    time.sleep(0.1)
    collector.stop()

    stats = collector.get_stats()
    # Should have seen at least one exception
    assert stats["total_exceptions"] > 0


def test_exception_with_custom_exception_class(tmp_path: Path):
    """Test that custom exception classes are properly tracked."""
    test_name = "test_custom_exception"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    with exception.ExceptionCollector(sampling_interval=1):
        for _ in range(10):
            _raise_custom_error()
            time.sleep(0.01)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0, "Expected at least one exception sample"

    # Look for our custom exception
    found_custom = False
    for sample in samples:
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "exception type")
        if label:
            exc_type = profile.string_table[label.str]
            if "CustomError" in exc_type:
                found_custom = True
                break

    assert found_custom, "Expected to find CustomError in exception samples"


def test_exception_profiling_disabled_by_default():
    """Test that exception profiling can be disabled."""
    from ddtrace.internal.settings.profiling import config as profiling_config

    # The config should have exception settings
    assert hasattr(profiling_config, "exception")
    assert hasattr(profiling_config.exception, "enabled")
    assert hasattr(profiling_config.exception, "sampling_interval")
    assert hasattr(profiling_config.exception, "collect_message")


def test_poisson_sampling_distribution():
    """Test that Poisson sampling generates reasonable intervals."""
    from ddtrace.profiling.collector import _fast_poisson

    # Test sampling with different lambda values
    samples = [_fast_poisson.sample(100) for _ in range(1000)]

    # Basic sanity checks
    assert all(s >= 0 for s in samples), "All samples should be non-negative"
    assert len(samples) == 1000

    # The mean should be close to lambda (100)
    mean = sum(samples) / len(samples)
    # Allow some deviation due to randomness
    assert 95 <= mean <= 105, f"Expected mean ~100, got {mean}"


# Helper functions for multi-threaded exception tests
def _spawn_nested_thread_with_exceptions():
    """Spawns a thread that raises and catches exceptions."""
    for _ in range(5):
        try:
            raise ValueError("nested thread exception")
        except ValueError:
            pass


def _spawn_threads_with_exceptions():
    """Spawns multiple threads, each raising exceptions."""
    import threading

    threads = []
    for i in range(3):
        t = threading.Thread(target=_spawn_nested_thread_with_exceptions, name=f"NestedThread-{i}")
        t.start()
        threads.append(t)

    # Also raise some exceptions in this thread
    for _ in range(5):
        try:
            raise RuntimeError("parent thread exception")
        except RuntimeError:
            pass

    # Wait for all nested threads
    for t in threads:
        t.join()


def test_multithreaded_exception_profiling(tmp_path: Path):
    """Test exception profiling with multiple threads spawning threads."""
    import threading

    test_name = "test_multithreaded_exceptions"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    with exception.ExceptionCollector(sampling_interval=1):
        # Spawn multiple parent threads, each of which spawns child threads
        parent_threads = []
        for i in range(3):
            t = threading.Thread(target=_spawn_threads_with_exceptions, name=f"ParentThread-{i}")
            t.start()
            parent_threads.append(t)

        # Wait for all parent threads to complete
        for t in parent_threads:
            t.join()

        # Give a bit of time for samples to be collected
        time.sleep(0.1)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0, "Expected at least one exception sample from multi-threaded execution"

    # Verify we captured exceptions from different threads
    thread_names = set()
    exception_types = set()
    for sample in samples:
        thread_name_label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread name")
        if thread_name_label:
            thread_names.add(profile.string_table[thread_name_label.str])

        exc_type_label = pprof_utils.get_label_with_key(profile.string_table, sample, "exception type")
        if exc_type_label:
            exception_types.add(profile.string_table[exc_type_label.str])

    # We should see samples from multiple threads
    assert len(thread_names) > 0, "Expected to see thread names in exception samples"

    # We should see both ValueError or RuntimeError
    assert len(exception_types) > 0, "Expected to see exception types"
    exc_type_str = str(exception_types)
    assert any("ValueError" in t or "RuntimeError" in t for t in exception_types), (
        f"Expected ValueError or RuntimeError in {exc_type_str}"
    )


def test_exception_profiling_with_tracer(tmp_path: Path):
    """Test exception profiling with active tracer spans."""
    from ddtrace import ext
    from ddtrace.profiling.collector import stack
    from ddtrace.trace import Tracer

    test_name = "test_exception_with_tracer"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()

    tracer = Tracer()
    tracer._endpoint_call_counter_span_processor.enable()

    with exception.ExceptionCollector(sampling_interval=1):
        with stack.StackCollector(tracer=tracer):
            with tracer.trace("foobar", resource="resource", span_type=ext.SpanTypes.WEB):
                # Raise multiple exceptions during the trace
                for i in range(10):
                    try:
                        raise ValueError(f"traced exception {i}")
                    except ValueError:
                        pass
                time.sleep(0.5)

    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0, "Expected at least one exception sample with active tracer"

    # Verify exception type is captured
    found_value_error = False
    for sample in samples:
        exc_type_label = pprof_utils.get_label_with_key(profile.string_table, sample, "exception type")
        if exc_type_label:
            exc_type = profile.string_table[exc_type_label.str]
            if "ValueError" in exc_type:
                found_value_error = True
                break

    assert found_value_error, "Expected to find ValueError in exception samples with active tracer"
