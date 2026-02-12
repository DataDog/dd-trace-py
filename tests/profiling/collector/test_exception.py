import _thread
import os
from pathlib import Path
import sys
import threading
import time

import pytest

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import exception
from tests.profiling.collector import pprof_utils


# Exception profiling requires Python 3.10+
pytestmark = pytest.mark.skipif(sys.version_info < (3, 10), reason="Exception profiling requires Python 3.10+")


def _setup_profiler(tmp_path, test_name):
    """Configure ddup and return the output filename for profile parsing."""
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())
    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="1.0", output_filename=pprof_prefix)
    ddup.start()
    return output_filename


# Helper functions to throw exceptions


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


def _nested_exception_handling():
    try:
        try:
            raise ValueError("inner error")
        except ValueError:
            raise RuntimeError("outer error")
    except RuntimeError:
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
    pass


def _raise_custom_error():
    try:
        raise CustomError("custom exception")
    except CustomError:
        pass


def _thread_raise_value_errors():
    for _ in range(5):
        try:
            raise ValueError("thread exception")
        except ValueError:
            pass


def _thread_raise_runtime_errors():
    for _ in range(5):
        try:
            raise RuntimeError("thread exception")
        except RuntimeError:
            pass


def test_exception_config_defaults():
    """Test that exception profiling config has expected default values."""
    from ddtrace.internal.settings.profiling import config as profiling_config

    assert profiling_config.exception.enabled is True
    assert profiling_config.exception.sampling_interval == 100
    assert profiling_config.exception.collect_message is True


def test_poisson_sampling_distribution():
    """Test that Poisson sampling mean is close to the configured lambda."""
    from ddtrace.profiling.collector import _fast_poisson

    samples = [_fast_poisson.sample(100) for _ in range(1000)]

    assert all(s >= 0 for s in samples), "All samples should be non-negative"

    mean = sum(samples) / len(samples)
    # Mean of 1000 exponential(100) samples has std ≈ 3.16; use wide bounds to avoid flakes
    assert 80 <= mean <= 120, f"Expected mean ~100, got {mean}"


# Pprof profile tests


def test_simple_exception_profiling(tmp_path: Path):
    """Test that exception type, stack locations, and thread info are captured."""
    output_filename = _setup_profiler(tmp_path, "test_simple_exception")

    with exception.ExceptionCollector(sampling_interval=1):
        for _ in range(10):
            _handle_value_error()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            exception_type="builtins\\.ValueError",
            locations=[
                pprof_utils.StackLocation(function_name="_raise_value_error", filename="test_exception.py", line_no=-1),
                pprof_utils.StackLocation(
                    function_name="_handle_value_error", filename="test_exception.py", line_no=-1
                ),
            ],
        ),
        print_samples_on_failure=True,
    )


def test_exception_stack_trace(tmp_path: Path):
    """Test that a multi-level call chain is captured in the stack trace."""
    output_filename = _setup_profiler(tmp_path, "test_exception_stack")

    with exception.ExceptionCollector(sampling_interval=1):
        for _ in range(10):
            _level_1()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            exception_type="builtins\\.RuntimeError",
            locations=[
                pprof_utils.StackLocation(function_name="_level_3", filename="test_exception.py", line_no=-1),
                pprof_utils.StackLocation(function_name="_level_2", filename="test_exception.py", line_no=-1),
                pprof_utils.StackLocation(function_name="_level_1", filename="test_exception.py", line_no=-1),
            ],
        ),
        print_samples_on_failure=True,
    )


def test_multiple_exception_types(tmp_path: Path):
    """Test that all distinct exception types are captured."""
    output_filename = _setup_profiler(tmp_path, "test_multiple_exceptions")

    with exception.ExceptionCollector(sampling_interval=1):
        for _ in range(10):
            _raise_value_error_handled()
            _raise_type_error_handled()
            _raise_runtime_error_handled()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0

    # Verify all three exception types are present
    for exc_type in ["builtins\\.ValueError", "builtins\\.TypeError", "builtins\\.RuntimeError"]:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(exception_type=exc_type),
            print_samples_on_failure=True,
        )


def test_nested_exception_handling(tmp_path: Path):
    """Test that both inner and outer exceptions are captured in nested try-except."""
    output_filename = _setup_profiler(tmp_path, "test_nested_exceptions")

    with exception.ExceptionCollector(sampling_interval=1):
        for _ in range(10):
            _nested_exception_handling()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0

    # Both the inner ValueError and outer RuntimeError should be captured
    for exc_type in ["builtins\\.ValueError", "builtins\\.RuntimeError"]:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                exception_type=exc_type,
                locations=[
                    pprof_utils.StackLocation(
                        function_name="_nested_exception_handling", filename="test_exception.py", line_no=-1
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )


def test_custom_exception_class(tmp_path: Path):
    """Test that custom exception classes are tracked with module-qualified names."""
    output_filename = _setup_profiler(tmp_path, "test_custom_exception")

    with exception.ExceptionCollector(sampling_interval=1):
        for _ in range(10):
            _raise_custom_error()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            exception_type=".*\\.CustomError",
            locations=[
                pprof_utils.StackLocation(
                    function_name="_raise_custom_error", filename="test_exception.py", line_no=-1
                ),
            ],
        ),
        print_samples_on_failure=True,
    )


def test_max_nframe_limit(tmp_path: Path):
    """Test that stack traces are truncated to max_nframe."""
    output_filename = _setup_profiler(tmp_path, "test_max_nframe")

    with exception.ExceptionCollector(sampling_interval=1, max_nframe=5):
        for _ in range(10):
            _handle_deep_exception()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0

    # Find samples with our ValueError and verify frame truncation
    for sample in samples:
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "exception type")
        if label and "ValueError" in profile.string_table[label.str]:
            # _deep_recursion has 20+ frames, but max_nframe=5 should truncate
            assert len(sample.location_id) == 5, (
                f"Expected == 5 frames with max_nframe=5 when truncated, got {len(sample.location_id)}"
            )
            break
    else:
        pytest.fail("No ValueError exception sample found")


def test_multithreaded_exception_profiling(tmp_path: Path):
    """Test exceptions from multiple threads are captured with correct types."""
    output_filename = _setup_profiler(tmp_path, "test_multithreaded")

    with exception.ExceptionCollector(sampling_interval=1):
        threads = []
        for i in range(3):
            t_val = threading.Thread(target=_thread_raise_value_errors, name=f"ExcThread-{i}")
            t_val.start()
            threads.append(t_val)
            t_rt = threading.Thread(target=_thread_raise_runtime_errors, name=f"RtThread-{i}")
            t_rt.start()
            threads.append(t_rt)

        for t in threads:
            t.join()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0

    # Both exception types should be present
    for exc_type in ["builtins\\.ValueError", "builtins\\.RuntimeError"]:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(exception_type=exc_type),
            print_samples_on_failure=True,
        )

    # Verify samples came from multiple threads
    thread_names = set()
    for sample in samples:
        label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread name")
        if label:
            thread_names.add(profile.string_table[label.str])
    assert len(thread_names) > 1, f"Expected multiple thread names, got: {thread_names}"


def test_exception_with_tracer(tmp_path: Path, tracer):
    """Test exception profiling captures samples during active tracer spans."""
    from ddtrace import ext
    from ddtrace.profiling.collector import stack

    output_filename = _setup_profiler(tmp_path, "test_exception_with_tracer")

    tracer._endpoint_call_counter_span_processor.enable()

    with exception.ExceptionCollector(sampling_interval=1):
        with stack.StackCollector(tracer=tracer):
            with tracer.trace("foobar", resource="resource", span_type=ext.SpanTypes.WEB):
                for _ in range(10):
                    try:
                        raise ValueError("traced exception")
                    except ValueError:
                        pass
                time.sleep(0.5)

    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            exception_type="builtins\\.ValueError",
        ),
        print_samples_on_failure=True,
    )


def test_exception_message_collection(tmp_path: Path):
    """Test that exception messages are collected."""
    output_filename = _setup_profiler(tmp_path, "test_exception_message_collection")

    with exception.ExceptionCollector(sampling_interval=1, collect_message=True):
        for _ in range(10):
            try:
                raise ValueError("test exception message")
            except ValueError:
                pass

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(exception_message="test exception message"),
        print_samples_on_failure=True,
    )
