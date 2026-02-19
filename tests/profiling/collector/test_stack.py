import _thread
import os
from pathlib import Path
import sys
import threading
import time
from typing import TYPE_CHECKING
from typing import Generator
from unittest.mock import patch
import uuid

import pytest
from pytest import FixtureRequest
from pytest import MonkeyPatch

from ddtrace import ext
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import stack
from ddtrace.trace import Tracer
from tests.conftest import get_original_test_name
from tests.profiling.collector import pprof_utils
from tests.profiling.collector import test_collector


if TYPE_CHECKING:
    from tests.profiling.collector.pprof_pb2 import Sample  # pyright: ignore[reportMissingModuleSource]


# Python 3.11.9 is not compatible with gevent, https://github.com/gevent/gevent/issues/2040
# https://github.com/python/cpython/issues/117983
# The fix was not backported to 3.11. The fix was first released in 3.12.5 for
# Python 3.12. Tested with Python 3.11.8 and 3.12.5 to confirm the issue.
GEVENT_COMPATIBLE_WITH_PYTHON_VERSION = os.getenv("DD_PROFILE_TEST_GEVENT", False) and (
    sys.version_info < (3, 11, 9) or sys.version_info >= (3, 12, 5)
)


def func1() -> None:
    return func2()


def func2() -> None:
    return func3()


def func3() -> None:
    return func4()


def func4() -> None:
    return func5()


def func5() -> None:
    return time.sleep(1)


# Use subprocess as ddup config persists across tests.
@pytest.mark.subprocess(
    env=dict(
        DD_PROFILING_MAX_FRAMES="5",
        DD_PROFILING_OUTPUT_PPROF="/tmp/test_collect_truncate",
    )
)
def test_collect_truncate() -> None:
    import os

    from ddtrace.profiling import profiler
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack import func1

    pprof_prefix = os.environ["DD_PROFILING_OUTPUT_PPROF"]
    output_filename = pprof_prefix + "." + str(os.getpid())

    max_nframes = int(os.environ["DD_PROFILING_MAX_FRAMES"])

    p = profiler.Profiler()
    p.start()

    func1()

    p.stop()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0
    for sample in samples:
        # stack adds one extra frame for "%d frames omitted" message
        # Also, it allows max_nframes + 1 frames, so we add 2 here.
        assert len(sample.location_id) <= max_nframes + 2, len(sample.location_id)


def test_stack_locations(tmp_path: Path) -> None:
    test_name = "test_stack_locations"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def baz() -> None:
        time.sleep(0.1)

    def bar() -> None:
        baz()

    def foo() -> None:
        bar()

    with stack.StackCollector():
        for _ in range(10):
            foo()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0

    expected_sample = pprof_utils.StackEvent(
        thread_id=_thread.get_ident(),
        thread_name="MainThread",
        locations=[
            pprof_utils.StackLocation(
                function_name="baz",
                filename="test_stack.py",
                line_no=baz.__code__.co_firstlineno + 1,
            ),
            pprof_utils.StackLocation(
                function_name="bar",
                filename="test_stack.py",
                line_no=bar.__code__.co_firstlineno + 1,
            ),
            pprof_utils.StackLocation(
                function_name="foo",
                filename="test_stack.py",
                line_no=foo.__code__.co_firstlineno + 1,
            ),
        ],
    )

    pprof_utils.assert_profile_has_sample(profile, samples=samples, expected_sample=expected_sample)


def test_push_span(tmp_path: Path, tracer: Tracer) -> None:
    test_name = "test_push_span"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    tracer._endpoint_call_counter_span_processor.enable()

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    with stack.StackCollector(
        tracer=tracer,
    ):
        with tracer.trace("foobar", resource=resource, span_type=span_type) as span:
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id
            for _ in range(10):
                time.sleep(0.1)
    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples_with_span_id = pprof_utils.get_samples_with_label_key(profile, "span id")

    samples: list[Sample] = []
    for sample in samples_with_span_id:
        locations = [pprof_utils.get_location_from_id(profile, location_id) for location_id in sample.location_id]
        if any(location.filename.endswith("test_stack.py") for location in locations):
            samples.append(sample)

    assert samples, "No sample found with locations in test_stack.py"

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            span_id=span_id,
            local_root_span_id=local_root_span_id,
            trace_type=span_type,
            trace_endpoint=resource,
        ),
        print_samples_on_failure=True,
    )


def test_push_span_unregister_thread(tmp_path: Path, monkeypatch: MonkeyPatch, tracer: Tracer) -> None:
    with patch("ddtrace.internal.datadog.profiling.stack.unregister_thread") as unregister_thread:
        tracer._endpoint_call_counter_span_processor.enable()

        test_name = "test_push_span_unregister_thread"
        pprof_prefix = str(tmp_path / test_name)
        output_filename = pprof_prefix + "." + str(os.getpid())

        assert ddup.is_available
        ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
        ddup.start()
        ddup.upload()

        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB

        def target_fun() -> None:
            for _ in range(10):
                time.sleep(0.1)

        with stack.StackCollector(
            tracer=tracer,
        ):
            with tracer.trace("foobar", resource=resource, span_type=span_type) as span:
                span_id = span.span_id
                local_root_span_id = span._local_root.span_id
                t = threading.Thread(target=target_fun)
                t.start()
                t.join()
                thread_id = t.ident
        ddup.upload(tracer=tracer)

        profile = pprof_utils.parse_newest_profile(output_filename)
        samples_with_span_id = pprof_utils.get_samples_with_label_key(profile, "span id")
        samples: list[Sample] = []
        for sample in samples_with_span_id:
            locations = [pprof_utils.get_location_from_id(profile, location_id) for location_id in sample.location_id]
            if any(location.filename.endswith("test_stack.py") for location in locations):
                samples.append(sample)

        assert samples, "No sample found with locations in test_stack.py"
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                span_id=span_id,
                local_root_span_id=local_root_span_id,
                trace_type=span_type,
                trace_endpoint=resource,
            ),
            print_samples_on_failure=True,
        )

        unregister_thread.assert_called_with(thread_id)


def test_push_non_web_span(tmp_path: Path, tracer: Tracer) -> None:
    tracer._endpoint_call_counter_span_processor.enable()

    test_name = "test_push_non_web_span"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.SQL

    with stack.StackCollector(
        tracer=tracer,
    ):
        with tracer.trace("foobar", resource=resource, span_type=span_type) as span:
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id
            for _ in range(10):
                time.sleep(0.1)
    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples_with_span_id = pprof_utils.get_samples_with_label_key(profile, "span id")
    samples: list[Sample] = []
    for sample in samples_with_span_id:
        locations = [pprof_utils.get_location_from_id(profile, location_id) for location_id in sample.location_id]
        if any(location.filename.endswith("test_stack.py") for location in locations):
            samples.append(sample)

    assert samples, "No sample found with locations in test_stack.py"
    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            span_id=span_id,
            local_root_span_id=local_root_span_id,
            trace_type=span_type,
            # trace_endpoint is not set for non-web spans
        ),
        print_samples_on_failure=True,
    )


def test_push_span_none_span_type(tmp_path: Path, tracer: Tracer) -> None:
    # Test for https://github.com/DataDog/dd-trace-py/issues/11141
    test_name = "test_push_span_none_span_type"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    tracer._endpoint_call_counter_span_processor.enable()

    resource = str(uuid.uuid4())

    with stack.StackCollector(
        tracer=tracer,
    ):
        # Explicitly set None span_type as the default could change in the
        # future.
        with tracer.trace("foobar", resource=resource, span_type=None) as span:
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id
            for _ in range(10):
                time.sleep(0.1)
    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples_with_span_id = pprof_utils.get_samples_with_label_key(profile, "span id")
    samples: list[Sample] = []
    for sample in samples_with_span_id:
        locations = [pprof_utils.get_location_from_id(profile, location_id) for location_id in sample.location_id]
        if any(location.filename.endswith("test_stack.py") for location in locations):
            samples.append(sample)

    assert samples, "No sample found with locations in test_stack.py"
    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            span_id=span_id,
            local_root_span_id=local_root_span_id,
            # span_type is None
            # trace_endpoint is not set for non-web spans
        ),
        print_samples_on_failure=True,
    )


def test_exception_collection(tmp_path: Path) -> None:
    test_name = "test_exception_collection"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    with stack.StackCollector():
        try:
            raise ValueError("hello")
        except Exception:
            time.sleep(1)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "exception type")

    # DEV: update the test once we have exception profiling for stack v2
    # using echion
    assert len(samples) == 0


def test_exception_collection_threads(tmp_path: Path) -> None:
    test_name = "test_exception_collection_threads"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    with stack.StackCollector():

        def target_fun() -> None:
            try:
                raise ValueError("hello")
            except Exception:
                time.sleep(1)

        threads = []
        for _ in range(10):
            t = threading.Thread(target=target_fun)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "exception type")

    assert len(samples) == 0


def test_exception_collection_trace(tmp_path: Path, tracer: Tracer) -> None:
    test_name = "test_exception_collection_trace"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    tracer._endpoint_call_counter_span_processor.enable()

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    with stack.StackCollector(tracer=tracer):
        with tracer.trace("foobar", resource="resource", span_type=ext.SpanTypes.WEB):
            try:
                raise ValueError("hello")
            except Exception:
                time.sleep(1)

    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "exception type")

    assert len(samples) == 0


def test_collect_once_with_class(tmp_path: Path) -> None:
    class SomeClass(object):
        @classmethod
        def sleep_class(cls) -> None:
            return cls().sleep_instance()

        def sleep_instance(self) -> None:
            for _ in range(10):
                time.sleep(0.1)

    test_name = "test_collect_once_with_class"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    with stack.StackCollector():
        SomeClass.sleep_class()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep_instance",
                    filename="test_stack.py",
                    line_no=SomeClass.sleep_instance.__code__.co_firstlineno + 2,
                ),
                pprof_utils.StackLocation(
                    function_name="sleep_class",
                    filename="test_stack.py",
                    line_no=SomeClass.sleep_class.__code__.co_firstlineno + 2,
                ),
                pprof_utils.StackLocation(
                    function_name="test_collect_once_with_class",
                    filename="test_stack.py",
                    line_no=test_collect_once_with_class.__code__.co_firstlineno + 20,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )


def test_collect_once_with_class_not_right_type(tmp_path: Path) -> None:
    """Test that the stack collector profiles methods with non-conventional parameter names.

    Verifies the profiler handles methods where parameters don't follow standard conventions
    (e.g., using 'foobar' instead of 'self' or 'cls').
    """

    class SomeClass(object):
        @classmethod
        def sleep_class(foobar, cls) -> None:  # pyright: ignore[reportSelfClsParameterName]
            return foobar().sleep_instance(cls)

        def sleep_instance(foobar, self) -> None:  # pyright: ignore[reportUnusedParameter, reportSelfClsParameterName]
            for _ in range(10):
                time.sleep(0.1)

    test_name = "test_collect_once_with_class"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    with stack.StackCollector():
        SomeClass.sleep_class(123)

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep_instance",
                    filename="test_stack.py",
                    line_no=SomeClass.sleep_instance.__code__.co_firstlineno + 2,
                ),
                pprof_utils.StackLocation(
                    function_name="sleep_class",
                    filename="test_stack.py",
                    line_no=SomeClass.sleep_class.__code__.co_firstlineno + 2,
                ),
                pprof_utils.StackLocation(
                    function_name="test_collect_once_with_class_not_right_type",
                    filename="test_stack.py",
                    line_no=test_collect_once_with_class_not_right_type.__code__.co_firstlineno + 26,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )


def _fib(n: int) -> int:
    if n == 1:
        return 1
    elif n == 0:
        return 0
    else:
        return _fib(n - 1) + _fib(n - 2)


@pytest.mark.skipif(
    not GEVENT_COMPATIBLE_WITH_PYTHON_VERSION,
    reason=f"gevent is not compatible with Python {'.'.join(map(str, tuple(sys.version_info)[:3]))}",
)
@pytest.mark.subprocess(ddtrace_run=True)
def test_collect_gevent_thread_task() -> None:
    from gevent import monkey

    monkey.patch_all()

    import os
    import threading
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.test_stack import _fib

    test_name = "test_collect_gevent_thread_task"
    pprof_prefix = "/tmp/" + test_name
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    # Start some (green)threads
    def _do_fib() -> None:
        for _ in range(5):
            # spend some time in CPU so the profiler can catch something
            # On a Mac w/ Apple M3 MAX with Python 3.11 it takes about 200ms to calculate _fib(32)
            # And _fib() is called 5 times so it should take about 1 second
            # We use 5 threads below so it should take about 5 seconds
            _fib(32)
            # Just make sure gevent switches threads/greenlets
            time.sleep(0)

    threads = []

    with stack.StackCollector():
        for i in range(5):
            t = threading.Thread(target=_do_fib, name=f"TestThread {i}")
            t.start()
            threads.append(t)
        for t in threads:
            t.join()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "task name")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_name="MainThread",
            task_name=r"Greenlet-\d+$",
            locations=[
                # Since we're using recursive function _fib(), we expect to have
                # multiple locations for _fib(n) = _fib(n-1) + _fib(n-2)
                pprof_utils.StackLocation(
                    filename="test_stack.py",
                    function_name="_fib",
                    line_no=_fib.__code__.co_firstlineno + 6,
                ),
                pprof_utils.StackLocation(
                    filename="test_stack.py",
                    function_name="_fib",
                    line_no=_fib.__code__.co_firstlineno + 6,
                ),
                pprof_utils.StackLocation(
                    filename="test_stack.py",
                    function_name="_fib",
                    line_no=_fib.__code__.co_firstlineno + 6,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )


def test_repr() -> None:
    test_collector._test_repr(
        stack.StackCollector,
        "StackCollector(status=<ServiceStatus.STOPPED: 'stopped'>, nframes=64, tracer=None)",
    )


# Tests from tests/profiling/collector/test_stack.py (v1)
# Function to use for stress-test of polling
MAX_FN_NUM = 30
FN_TEMPLATE = """def _f{num}():
  return _f{nump1}()"""

for num in range(MAX_FN_NUM):
    exec(FN_TEMPLATE.format(num=num, nump1=num + 1))

exec(
    """def _f{MAX_FN_NUM}():
    try:
      raise ValueError('test')
    except Exception:
      time.sleep(2)""".format(MAX_FN_NUM=MAX_FN_NUM)
)


def test_stress_threads_run_as_thread(tmp_path: Path) -> None:
    test_name = "test_stress_threads_run_as_thread"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    quit_thread = threading.Event()

    def wait_for_quit() -> None:
        quit_thread.wait()

    with stack.StackCollector():
        NB_THREADS = 40

        threads = []
        for _ in range(NB_THREADS):
            t = threading.Thread(target=wait_for_quit)
            t.start()
            threads.append(t)

        time.sleep(3)

        quit_thread.set()
        for t in threads:
            t.join()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0


# if you don't need to check the output profile, you can use this fixture
@pytest.fixture
def tracer_and_collector(
    tracer: Tracer, request: FixtureRequest, tmp_path: Path
) -> Generator[tuple[Tracer, stack.StackCollector], None, None]:
    test_name = get_original_test_name(request)
    pprof_prefix = str(tmp_path / test_name)

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    c = stack.StackCollector(tracer=tracer)
    c.start()
    try:
        yield tracer, c
    finally:
        c.stop()
        ddup.upload(tracer=tracer)


def test_collect_span_id(tracer: Tracer, tmp_path: Path) -> None:
    test_name = "test_collect_span_id"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    tracer._endpoint_call_counter_span_processor.enable()
    with stack.StackCollector(tracer=tracer):
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        with tracer.start_span("foobar", activate=True, resource=resource, span_type=span_type) as span:
            for _ in range(10):
                time.sleep(0.1)
            span_id = span.span_id
            local_root_span_id = span._local_root.span_id

    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "trace endpoint")
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            span_id=span_id,
            trace_type=span_type,
            local_root_span_id=local_root_span_id,
            trace_endpoint=resource,
            locations=[
                pprof_utils.StackLocation(
                    filename=os.path.basename(__file__),
                    function_name=test_name,
                    line_no=test_collect_span_id.__code__.co_firstlineno + 16,
                )
            ],
        ),
        print_samples_on_failure=True,
    )


def test_collect_span_resource_after_finish(tracer: Tracer, tmp_path: Path, request: FixtureRequest) -> None:
    test_name = get_original_test_name(request)
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    tracer._endpoint_call_counter_span_processor.enable()
    with stack.StackCollector(tracer=tracer):
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        span = tracer.start_span("foobar", activate=True, span_type=span_type, resource=resource)
        for _ in range(10):
            time.sleep(0.1)
    ddup.upload(tracer=tracer)
    span.finish()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = profile.sample
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            span_id=span.span_id,
            trace_type=span_type,
            # Looks like the endpoint is not collected if the span is not finished
            # trace_endpoint=resource,
            locations=[
                pprof_utils.StackLocation(
                    filename=os.path.basename(__file__),
                    function_name=test_name,
                    line_no=test_collect_span_resource_after_finish.__code__.co_firstlineno + 15,
                )
            ],
        ),
        print_samples_on_failure=True,
    )


def test_resource_not_collected(tmp_path: Path, tracer: Tracer) -> None:
    test_name = "test_resource_not_collected"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    with stack.StackCollector(tracer=tracer):
        # Give the Profiler some time to start
        time.sleep(0.1)

        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        with tracer.start_span("foobar", activate=True, resource=resource, span_type=span_type) as span:
            _fib(35)

    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    pprof_utils.assert_profile_has_sample(
        profile,
        profile.sample,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            span_id=span.span_id,
            trace_type=span_type,
            locations=[
                pprof_utils.StackLocation(
                    filename=os.path.basename(__file__),
                    function_name=test_name,
                    line_no=test_resource_not_collected.__code__.co_firstlineno + 17,
                )
            ],
        ),
        print_samples_on_failure=True,
    )


def test_collect_nested_span_id(tmp_path: Path, tracer: Tracer, request: FixtureRequest) -> None:
    test_name = get_original_test_name(request)
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    tracer._endpoint_call_counter_span_processor.enable()
    with stack.StackCollector(tracer=tracer):
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB
        with tracer.start_span("foobar", activate=True, resource=resource, span_type=span_type):
            with tracer.start_span("foobar", activate=True, resource=resource, span_type=span_type) as child_span:
                for _ in range(10):
                    time.sleep(0.1)
    ddup.upload(tracer=tracer)

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_label_key(profile, "span id")
    pprof_utils.assert_profile_has_sample(
        profile,
        samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            span_id=child_span.span_id,
            trace_type=span_type,
            local_root_span_id=child_span._local_root.span_id,
            trace_endpoint=resource,
            locations=[
                pprof_utils.StackLocation(
                    filename=os.path.basename(__file__),
                    function_name=test_name,
                    line_no=test_collect_nested_span_id.__code__.co_firstlineno + 17,
                )
            ],
        ),
        print_samples_on_failure=True,
    )


def test_stress_trace_collection(tracer_and_collector: tuple[Tracer, stack.StackCollector]) -> None:
    tracer, _ = tracer_and_collector

    def _trace() -> None:
        for _ in range(5000):
            with tracer.trace("hello"):
                time.sleep(0.001)

    NB_THREADS = 30

    threads = []
    for _ in range(NB_THREADS):
        t = threading.Thread(target=_trace)
        threads.append(t)

    for t in threads:
        t.start()

    for t in threads:
        t.join()


def test_top_c_frame_detection(tmp_path: Path) -> None:
    """Test that the top-most native C frame is tracked and appears as the leaf frame in the profile."""
    test_name = "test_top_c_frame_detection"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sleep_loop() -> None:
        for _ in range(10):
            time.sleep(0.1)

    with stack.StackCollector():
        sleep_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "wall-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[
                pprof_utils.StackLocation(
                    function_name="sleep",
                    filename="",
                    line_no=-1,
                ),
                pprof_utils.StackLocation(
                    function_name="sleep_loop",
                    filename="test_stack.py",
                    line_no=-1,
                ),
            ],
        ),
        print_samples_on_failure=True,
    )


FILE_NAME = os.path.basename(__file__)


def loc(function_name: str, filename: str = FILE_NAME, line_no: int = -1) -> pprof_utils.StackLocation:
    return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)


@pytest.mark.subprocess()
def test_top_c_frame_detection_hashlib() -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import _thread
    import hashlib
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_hashlib"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sha256_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            hashlib.sha256(b"Hello, world!")

    with stack.StackCollector():
        sha256_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("sha256"), loc("sha256_loop", FILE_NAME)],
        [loc("monotonic"), loc("sha256_loop", FILE_NAME)],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(),
                thread_name="MainThread",
                locations=exp_stack,
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess()
def test_top_c_frame_detection_hashlib_kwarg() -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import _thread
    import hashlib
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_hashlib"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sha256_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            hashlib.sha256(data=b"Hello, world!")

    with stack.StackCollector():
        sha256_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("sha256"), loc("sha256_loop", FILE_NAME)],
        [loc("monotonic"), loc("sha256_loop", FILE_NAME)],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess(
    env=dict(
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    )
)
def test_top_c_frame_detection_numpy_flat() -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    import numpy as np

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_hashlib"
    pprof_prefix = str(tmp_path / test_name)

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def numpy_loop() -> None:
        test_start = time.monotonic()
        while time.monotonic() - test_start < 2:
            mat = np.random.rand(1000, 1000)
            np.matmul(mat, mat)

    with stack.StackCollector():
        numpy_loop()

    ddup.upload()

    all_files_for_pid = sorted([x for x in os.listdir(tmp_path) if ".pprof" in x])
    parent_files = [str(tmp_path / f) for f in all_files_for_pid if f".{str(os.getpid())}." in f]
    profiles = [pprof_utils.parse_profile(f) for f in parent_files]
    profile = pprof_utils.merge_profiles(profiles)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("matmul"), loc("numpy_loop", FILE_NAME)],
        [loc("rand"), loc("numpy_loop", FILE_NAME)],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess(
    env=dict(
        _DD_PROFILING_STACK_ADAPTIVE_SAMPLING_ENABLED="0",
    )
)
def test_top_c_frame_detection_numpy_nested() -> None:
    """Test that hashlib.sha256 appears as the top-most native C frame in the profile."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    import numpy as np

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_hashlib"
    pprof_prefix = str(tmp_path / test_name)

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def numpy_loop() -> None:
        test_start = time.monotonic()
        while time.monotonic() - test_start < 2:
            np.matmul(np.random.rand(1000, 1000), np.random.rand(1000, 1000))

    with stack.StackCollector():
        numpy_loop()

    ddup.upload()

    all_files_for_pid = sorted([x for x in os.listdir(tmp_path) if ".pprof" in x])
    parent_files = [str(tmp_path / f) for f in all_files_for_pid if f".{str(os.getpid())}." in f]
    print(f"Parent files: {parent_files}")
    profiles = [pprof_utils.parse_profile(f) for f in parent_files]
    profile = pprof_utils.merge_profiles(profiles)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("matmul"), loc("numpy_loop", FILE_NAME)],
        [loc("rand"), loc("numpy_loop", FILE_NAME)],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess()
def test_top_c_frame_detection_zlib() -> None:
    """Test module-level C function: zlib.compress (LOAD_GLOBAL → LOAD_ATTR → PUSH_NULL → CALL)."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time
    import zlib

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_zlib"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def zlib_loop() -> None:
        data = b"x" * 100000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            zlib.compress(data)

    with stack.StackCollector():
        zlib_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("compress"), loc("zlib_loop")],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess()
def test_top_c_frame_detection_sorted_builtin() -> None:
    """Test builtin function: sorted() (LOAD_GLOBAL+NULL → CALL, no LOAD_ATTR)."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_sorted_builtin"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sorted_loop() -> None:
        data = list(range(10000, 0, -1))
        end = time.monotonic() + 2
        while time.monotonic() < end:
            sorted(data)

    with stack.StackCollector():
        sorted_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("sorted"), loc("sorted_loop")],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess()
def test_top_c_frame_detection_list_sort_method() -> None:
    """Test method call: list.sort() (LOAD_FAST → LOAD_ATTR method → CALL)."""
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_list_sort"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sort_loop() -> None:
        original = list(range(10000, 0, -1))
        end = time.monotonic() + 2
        while time.monotonic() < end:
            data = original.copy()
            data.sort()

    with stack.StackCollector():
        sort_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("sort"), loc("sort_loop")],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess()
def test_top_c_frame_detection_regex_method() -> None:
    """Test method call on C object: Pattern.findall() (LOAD_FAST → LOAD_ATTR method → LOAD_FAST → CALL)."""
    import _thread
    import os
    import pathlib
    import re
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_regex"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def regex_loop() -> None:
        pattern = re.compile(r"[a-z]+")
        text = "hello world foo bar baz " * 10000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            pattern.findall(text)

    with stack.StackCollector():
        regex_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("findall"), loc("regex_loop")],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess()
def test_top_c_frame_detection_expression_arg() -> None:
    """Test C call with expression as argument: zlib.compress(chunk_a + chunk_b).

    Exercises BINARY_OP and LOAD_FAST_BORROW_LOAD_FAST_BORROW in depth tracking.
    Bytecode: LOAD_GLOBAL → LOAD_ATTR → PUSH_NULL → LOAD_FAST_x2 → BINARY_OP → CALL.
    """
    import _thread
    import os
    import pathlib
    import tempfile
    import time
    import zlib

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_expression_arg"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def compress_concat_loop() -> None:
        chunk_a = b"x" * 50000
        chunk_b = b"y" * 50000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            zlib.compress(chunk_a + chunk_b)

    with stack.StackCollector():
        compress_concat_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("compress"), loc("compress_concat_loop")],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess()
def test_top_c_frame_detection_nested_c_calls() -> None:
    """Test nested C calls: hashlib.sha256(zlib.compress(data)).

    When caught in sha256, backward scan must skip past the inner CALL (compress)
    and its arguments to reach the LOAD_ATTR for sha256.
    """
    import _thread
    import hashlib
    import os
    import pathlib
    import tempfile
    import time
    import zlib

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_nested_c_calls"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def nested_loop() -> None:
        data = os.urandom(100000)
        end = time.monotonic() + 2
        while time.monotonic() < end:
            hashlib.sha256(zlib.compress(data))

    with stack.StackCollector():
        nested_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("sha256"), loc("nested_loop")],
        [loc("compress"), loc("nested_loop")],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )


@pytest.mark.subprocess()
def test_top_c_frame_detection_call_kw() -> None:
    """Test builtin with keyword argument: sorted(data, reverse=True).

    Exercises CALL_KW opcode which consumes an extra kwnames tuple.
    Bytecode: LOAD_GLOBAL+NULL → LOAD_FAST → LOAD_CONST → LOAD_CONST (kwnames) → CALL_KW.
    """
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_call_kw"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sorted_kw_loop() -> None:
        data = list(range(10000, 0, -1))
        end = time.monotonic() + 2
        while time.monotonic() < end:
            sorted(data, reverse=True)

    with stack.StackCollector():
        sorted_kw_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("sorted"), loc("sorted_kw_loop")],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess()
def test_top_c_frame_detection_many_args() -> None:
    """Test C function with many positional arguments: hashlib.pbkdf2_hmac(algo, pwd, salt, iters).

    Depth tracking must skip past 4 LOAD_CONST/LOAD_FAST + PUSH_NULL before reaching LOAD_ATTR.
    """
    import _thread
    import hashlib
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_many_args"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def pbkdf2_loop() -> None:
        end = time.monotonic() + 2
        while time.monotonic() < end:
            hashlib.pbkdf2_hmac("sha256", b"password", b"salt", 50000)

    with stack.StackCollector():
        pbkdf2_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("pbkdf2_hmac"), loc("pbkdf2_loop")],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess()
def test_top_c_frame_detection_method_two_args() -> None:
    """Test C method call with 2 arguments: Pattern.sub(repl, text).

    Bytecode: LOAD_FAST → LOAD_ATTR (method) → LOAD_CONST → LOAD_FAST → CALL 2.
    """
    import _thread
    import os
    import pathlib
    import re
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_method_two_args"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def regex_sub_loop() -> None:
        pattern = re.compile(r"[a-z]+")
        text = "hello world foo bar baz " * 10000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            pattern.sub("X", text)

    with stack.StackCollector():
        regex_sub_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("sub"), loc("regex_sub_loop")],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess()
def test_top_c_frame_detection_method_on_literal() -> None:
    """Test method call on a literal: b''.join(chunks).

    Receiver is LOAD_CONST (not LOAD_FAST/LOAD_GLOBAL), consumed by LOAD_ATTR method variant.
    Bytecode: LOAD_CONST b'' → LOAD_ATTR (join, method) → LOAD_FAST → CALL 1.
    """
    import _thread
    import os
    import pathlib
    import tempfile
    import time

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_method_on_literal"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def join_loop() -> None:
        chunks = [b"x" * 1000] * 1000
        end = time.monotonic() + 2
        while time.monotonic() < end:
            b"".join(chunks)

    with stack.StackCollector():
        join_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    pprof_utils.assert_profile_has_sample(
        profile,
        samples=samples,
        expected_sample=pprof_utils.StackEvent(
            thread_id=_thread.get_ident(),
            thread_name="MainThread",
            locations=[loc("join"), loc("join_loop")],
        ),
        print_samples_on_failure=True,
    )


@pytest.mark.subprocess()
def test_top_c_frame_detection_sequential_calls() -> None:
    """Test multiple sequential C calls in one function: compress then sha256.

    Both C frames should appear in the profile with distinct callable names.
    Tests that STORE_FAST between calls doesn't confuse the backward scan.
    """
    import _thread
    import hashlib
    import os
    import pathlib
    import tempfile
    import time
    import zlib

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import stack
    from tests.profiling.collector import pprof_utils

    FILE_NAME = os.path.basename(__file__)

    def loc(function_name, filename=FILE_NAME, line_no=-1):
        return pprof_utils.StackLocation(function_name=function_name, filename=filename, line_no=line_no)

    tmp_path = pathlib.Path(tempfile.mkdtemp())
    test_name = "test_top_c_frame_detection_sequential_calls"
    pprof_prefix = str(tmp_path / test_name)
    output_filename = pprof_prefix + "." + str(os.getpid())

    assert ddup.is_available
    ddup.config(env="test", service=test_name, version="my_version", output_filename=pprof_prefix)
    ddup.start()
    ddup.upload()

    def sequential_loop() -> None:
        data = os.urandom(100000)
        end = time.monotonic() + 2
        while time.monotonic() < end:
            zlib.compress(data)
            hashlib.sha256(data)

    with stack.StackCollector():
        sequential_loop()

    ddup.upload()

    profile = pprof_utils.parse_newest_profile(output_filename)
    samples = pprof_utils.get_samples_with_value_type(profile, "cpu-time")
    assert len(samples) > 0

    expected_stacks = [
        [loc("compress"), loc("sequential_loop")],
        [loc("sha256"), loc("sequential_loop")],
    ]

    for exp_stack in expected_stacks:
        pprof_utils.assert_profile_has_sample(
            profile,
            samples=samples,
            expected_sample=pprof_utils.StackEvent(
                thread_id=_thread.get_ident(), thread_name="MainThread", locations=exp_stack
            ),
            print_samples_on_failure=True,
        )
