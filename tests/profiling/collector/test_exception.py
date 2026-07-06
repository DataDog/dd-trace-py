import inspect
import sys
import threading
import time
from unittest import mock

import pytest


# Exception profiling requires Python 3.12
pytestmark = pytest.mark.skipif(sys.version_info < (3, 12), reason="Exception profiling requires Python 3.12+")


# Helper functions to throw exceptions


def _raise_value_error() -> None:
    raise ValueError("test value error")


def _handle_value_error() -> None:
    try:
        _raise_value_error()
    except ValueError:
        pass


def _level_3() -> None:
    raise RuntimeError("deep error")


def _level_2() -> None:
    _level_3()


def _level_1() -> None:
    try:
        _level_2()
    except RuntimeError:
        pass


def _raise_value_error_handled() -> None:
    try:
        raise ValueError("value error")
    except ValueError:
        pass


def _raise_type_error_handled() -> None:
    try:
        raise TypeError("type error")
    except TypeError:
        pass


def _raise_runtime_error_handled() -> None:
    try:
        raise RuntimeError("runtime error")
    except RuntimeError:
        pass


def _wrapped_exception_handling() -> None:
    try:
        try:
            raise ValueError("inner error")
        except ValueError:
            raise RuntimeError("outer error")
    except RuntimeError:
        pass


def _deep_recursion(depth: int) -> None:
    if depth == 0:
        raise ValueError("deep error")
    return _deep_recursion(depth - 1)


def _handle_deep_exception() -> None:
    try:
        _deep_recursion(20)
    except ValueError:
        pass


class CustomError(Exception):
    pass


def _raise_custom_error() -> None:
    try:
        raise CustomError("custom exception")
    except CustomError:
        pass


def _thread_raise_value_errors() -> None:
    for _ in range(5):
        try:
            raise ValueError("thread exception")
        except ValueError:
            pass


def _thread_raise_runtime_errors() -> None:
    for _ in range(5):
        try:
            raise RuntimeError("thread exception")
        except RuntimeError:
            pass


def _raise_long_exception_message() -> None:
    try:
        raise ValueError("a" * 1000)
    except ValueError:
        pass


def _lineno_of(func, substring):
    """Return the 1-based line number of the first source line of func containing substring."""
    source_lines, start_lineno = inspect.getsourcelines(func)
    for offset, line in enumerate(source_lines):
        if substring in line:
            return start_lineno + offset
    raise AssertionError(f"{substring!r} not found in source of {func.__name__}")


def test_exception_config_defaults() -> None:
    """Test that exception profiling config has expected default values."""
    from ddtrace.internal.settings.profiling import config as profiling_config

    assert profiling_config.exception.enabled is False
    assert profiling_config.exception.sampling_interval == 100
    assert profiling_config.exception.collect_message is False


def test_poisson_sampling_distribution() -> None:
    """Test that Poisson sampling mean is close to the configured lambda."""
    from ddtrace.profiling.collector._fast_poisson import PoissonSampler

    sampler = PoissonSampler()
    samples = [sampler.sample(100) for _ in range(1000)]

    assert all(s >= 0 for s in samples), "All samples should be non-negative"

    mean = sum(samples) / len(samples)
    # Mean of 1000 exponential(100) samples has std ≈ 3.16; use wide bounds to avoid flakes
    assert 80 <= mean <= 120, f"Expected mean ~100, got {mean}"


# Pprof profile tests


@pytest.mark.subprocess(err=None)
def test_simple_exception_profiling() -> None:
    """Test that exception type, stack locations, and thread info are captured."""
    import _thread

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _handle_value_error
    from tests.profiling.collector.test_exception import _lineno_of
    from tests.profiling.collector.test_exception import _raise_value_error
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_simple_exception", version="1.0")
        ddup.start()

        with exception.ExceptionCollector(sampling_interval=1):
            for _ in range(10):
                _handle_value_error()

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
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
                    pprof_utils.StackLocation(
                        function_name="_raise_value_error",
                        filename="test_exception.py",
                        line_no=_lineno_of(_raise_value_error, "raise ValueError"),
                    ),
                    pprof_utils.StackLocation(
                        function_name="_handle_value_error",
                        filename="test_exception.py",
                        line_no=_lineno_of(_handle_value_error, "_raise_value_error()"),
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess(err=None)
def test_exception_stack_trace() -> None:
    """Test that a multi-level call chain is captured in the stack trace."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _level_1
    from tests.profiling.collector.test_exception import _level_2
    from tests.profiling.collector.test_exception import _level_3
    from tests.profiling.collector.test_exception import _lineno_of
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_exception_stack", version="1.0")
        ddup.start()

        with exception.ExceptionCollector(sampling_interval=1):
            for _ in range(10):
                _level_1()

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
        samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
        assert len(samples) > 0

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                exception_type="builtins\\.RuntimeError",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="_level_3",
                        filename="test_exception.py",
                        line_no=_lineno_of(_level_3, "raise RuntimeError"),
                    ),
                    pprof_utils.StackLocation(
                        function_name="_level_2",
                        filename="test_exception.py",
                        line_no=_lineno_of(_level_2, "_level_3()"),
                    ),
                    pprof_utils.StackLocation(
                        function_name="_level_1",
                        filename="test_exception.py",
                        line_no=_lineno_of(_level_1, "_level_2()"),
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess(err=None)
def test_multiple_exception_types() -> None:
    """Test that all distinct exception types are captured."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _raise_runtime_error_handled
    from tests.profiling.collector.test_exception import _raise_type_error_handled
    from tests.profiling.collector.test_exception import _raise_value_error_handled
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_multiple_exceptions", version="1.0")
        ddup.start()

        with exception.ExceptionCollector(sampling_interval=1):
            for _ in range(10):
                _raise_value_error_handled()
                _raise_type_error_handled()
                _raise_runtime_error_handled()

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
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


@pytest.mark.subprocess(err=None)
def test_wrapped_exception_handling() -> None:
    """Test that both inner and outer exceptions are captured in wrapped try-except."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _lineno_of
    from tests.profiling.collector.test_exception import _wrapped_exception_handling
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_wrapped_exceptions", version="1.0")
        ddup.start()

        with exception.ExceptionCollector(sampling_interval=1):
            for _ in range(10):
                _wrapped_exception_handling()

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
        samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
        assert len(samples) > 0

        # Both the inner ValueError and outer RuntimeError should be captured
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                exception_type="builtins\\.ValueError",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="_wrapped_exception_handling",
                        filename="test_exception.py",
                        line_no=_lineno_of(_wrapped_exception_handling, 'raise ValueError("inner error")'),
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                exception_type="builtins\\.RuntimeError",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="_wrapped_exception_handling",
                        filename="test_exception.py",
                        line_no=_lineno_of(_wrapped_exception_handling, 'raise RuntimeError("outer error")'),
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess(err=None)
def test_custom_exception_class() -> None:
    """Test that custom exception classes are tracked with module-qualified names."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _lineno_of
    from tests.profiling.collector.test_exception import _raise_custom_error
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_custom_exception", version="1.0")
        ddup.start()

        with exception.ExceptionCollector(sampling_interval=1):
            for _ in range(10):
                _raise_custom_error()

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
        samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
        assert len(samples) > 0

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                exception_type=".*\\.CustomError",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="_raise_custom_error",
                        filename="test_exception.py",
                        line_no=_lineno_of(_raise_custom_error, "raise CustomError"),
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess(err=None)
def test_long_exception_message() -> None:
    """Test that long exception messages are truncated."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _raise_long_exception_message
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_long_exception_message", version="1.0")
        ddup.start()

        with exception.ExceptionCollector(sampling_interval=1, collect_message=True):
            for _ in range(10):
                _raise_long_exception_message()

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
        samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
        assert len(samples) > 0

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(exception_message=f"{'a' * 128}\\.\\.\\. \\(truncated\\)"),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess(err=None)
def test_multithreaded_exception_profiling() -> None:
    """Test exceptions from multiple threads are captured with correct types."""


    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _thread_raise_runtime_errors
    from tests.profiling.collector.test_exception import _thread_raise_value_errors
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_multithreaded", version="1.0")
        ddup.start()

        with exception.ExceptionCollector(sampling_interval=1):
            threads: list[threading.Thread] = []
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

        profile = pprof_utils.get_profile_from_agent(agent_client)
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
        thread_names: set[str] = set()
        for sample in samples:
            label = pprof_utils.get_label_with_key(profile.string_table, sample, "thread name")
            if label:
                thread_names.add(profile.string_table[label.str])
        assert len(thread_names) > 1, f"Expected multiple thread names, got: {thread_names}"


@pytest.mark.subprocess(err=None)
def test_exception_with_tracer() -> None:
    """Test exception profiling captures samples during active tracer spans."""
    import time

    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from ddtrace.profiling.collector import stack
    from ddtrace.trace import tracer
    from tests.profiling.collector import pprof_utils
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available

    tracer._endpoint_call_counter_span_processor.enable()

    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_exception_with_tracer", version="1.0")
        ddup.start()

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

        profile = pprof_utils.get_profile_from_agent(agent_client)
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


@pytest.mark.subprocess(err=None)
def test_exception_message_collection() -> None:
    """Test that exception messages are collected."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_exception_message_collection", version="1.0")
        ddup.start()

        with exception.ExceptionCollector(sampling_interval=1, collect_message=True):
            for _ in range(10):
                try:
                    raise ValueError("test exception message")
                except ValueError:
                    pass

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
        samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
        assert len(samples) > 0

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(exception_message="test exception message"),
            print_samples_on_failure=True,
        )


# Callable type helpers for instrumentation coverage tests


class _ExceptionInMethod:
    def handle(self) -> None:
        try:
            raise ValueError("method error")
        except ValueError:
            pass


class _ExceptionInStaticMethod:
    @staticmethod
    def handle() -> None:
        try:
            raise ValueError("static error")
        except ValueError:
            pass


class _ExceptionInClassMethod:
    @classmethod
    def handle(cls) -> None:
        try:
            raise ValueError("classmethod error")
        except ValueError:
            pass


class _CallableWithException:
    def __call__(self) -> None:
        try:
            raise ValueError("callable error")
        except ValueError:
            pass


@pytest.mark.subprocess(err=None)
def test_exception_in_instance_method() -> None:
    """Test that exceptions in instance methods are captured."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _ExceptionInMethod
    from tests.profiling.collector.test_exception import _lineno_of
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_instance_method", version="1.0")
        ddup.start()

        obj = _ExceptionInMethod()
        with exception.ExceptionCollector(sampling_interval=1):
            for _ in range(10):
                obj.handle()

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
        samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
        assert len(samples) > 0

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                exception_type="builtins\\.ValueError",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="handle",
                        filename="test_exception.py",
                        line_no=_lineno_of(_ExceptionInMethod.handle, "raise ValueError"),
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess(err=None)
def test_exception_in_static_method() -> None:
    """Test that exceptions in static methods are captured."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _ExceptionInStaticMethod
    from tests.profiling.collector.test_exception import _lineno_of
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_static_method", version="1.0")
        ddup.start()

        with exception.ExceptionCollector(sampling_interval=1):
            for _ in range(10):
                _ExceptionInStaticMethod.handle()

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
        samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
        assert len(samples) > 0

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                exception_type="builtins\\.ValueError",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="handle",
                        filename="test_exception.py",
                        line_no=_lineno_of(_ExceptionInStaticMethod.handle, "raise ValueError"),
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess(err=None)
def test_exception_in_class_method() -> None:
    """Test that exceptions in class methods are captured."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _ExceptionInClassMethod
    from tests.profiling.collector.test_exception import _lineno_of
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_class_method", version="1.0")
        ddup.start()

        with exception.ExceptionCollector(sampling_interval=1):
            for _ in range(10):
                _ExceptionInClassMethod.handle()

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
        samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
        assert len(samples) > 0

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                exception_type="builtins\\.ValueError",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="handle",
                        filename="test_exception.py",
                        line_no=_lineno_of(_ExceptionInClassMethod.handle, "raise ValueError"),
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess(err=None)
def test_exception_in_callable_instance() -> None:
    """Test that exceptions in callable instances (__call__) are captured."""
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import exception
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_exception import _CallableWithException
    from tests.profiling.collector.test_exception import _lineno_of
    from tests.profiling.utils import with_profiling_test_agent

    assert ddup.is_available
    with with_profiling_test_agent() as agent_client:
        ddup.config(env="test", service="test_callable_instance", version="1.0")
        ddup.start()

        obj = _CallableWithException()
        with exception.ExceptionCollector(sampling_interval=1):
            for _ in range(10):
                obj()

        ddup.upload()

        profile = pprof_utils.get_profile_from_agent(agent_client)
        samples = pprof_utils.get_samples_with_value_type(profile, "exception-samples")
        assert len(samples) > 0

        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                exception_type="builtins\\.ValueError",
                locations=[
                    pprof_utils.StackLocation(
                        function_name="__call__",
                        filename="test_exception.py",
                        line_no=_lineno_of(_CallableWithException.__call__, "raise ValueError"),
                    ),
                ],
            ),
            print_samples_on_failure=True,
        )


def test_exception_uses_push_monotonic_ns() -> None:
    """Verify the exception collector calls push_monotonic_ns with a timestamp in [before, after]."""
    import ddtrace.profiling.collector._exception as _exception_module
    from ddtrace.profiling.collector.exception import ExceptionCollector

    mock_handle: mock.MagicMock = mock.MagicMock()

    before: int = time.monotonic_ns()
    with (
        mock.patch.object(_exception_module, "ddup") as mock_ddup,
        ExceptionCollector(sampling_interval=1),
    ):
        mock_ddup.SampleHandle.return_value = mock_handle

        for _ in range(10):
            try:
                raise ValueError("test")
            except ValueError:
                pass
    after: int = time.monotonic_ns()

    assert mock_handle.push_monotonic_ns.called, "push_monotonic_ns should be called"
    assert not mock_handle.push_absolute_ns.called, "push_absolute_ns should not be called"
    for call in mock_handle.push_monotonic_ns.call_args_list:
        ts: int = call[0][0]
        assert isinstance(ts, int), f"Expected int timestamp, got {type(ts)}"
        assert before <= ts <= after, f"Expected monotonic timestamp in [{before}, {after}], got {ts}"
