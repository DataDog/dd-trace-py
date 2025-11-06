import _thread
import glob
import os
import threading
from typing import Callable
from typing import List
from typing import Optional
from typing import Type
from typing import Union
import uuid

import mock
import pytest

from ddtrace import ext
from ddtrace._trace.span import Span
from ddtrace._trace.tracer import Tracer
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector.threading import ThreadingLockCollector
from ddtrace.profiling.collector.threading import ThreadingRLockCollector
from tests.profiling.collector import pprof_utils
from tests.profiling.collector import test_collector
from tests.profiling.collector.lock_utils import LineNo
from tests.profiling.collector.lock_utils import get_lock_linenos
from tests.profiling.collector.lock_utils import init_linenos
from tests.profiling.collector.pprof_utils import pprof_pb2


# Type aliases for supported classes
LockClassType = Union[Type[threading.Lock], Type[threading.RLock]]
CollectorClassType = Union[Type[ThreadingLockCollector], Type[ThreadingRLockCollector]]
# threading.Lock and threading.RLock are factory functions that return _thread types.
# We reference the underlying _thread types directly to avoid creating instances at import time.
LockClassInst = Union[_thread.LockType, _thread.RLock]

# Module-level globals for testing global lock profiling
_test_global_lock: LockClassInst


class TestBar:
    ...


_test_global_bar_instance: TestBar

init_linenos(__file__)


# Helper classes for testing lock collector
class Foo:
    def __init__(self, lock_class: LockClassType) -> None:
        self.foo_lock: LockClassInst = lock_class()  # !CREATE! foolock

    def foo(self) -> None:
        with self.foo_lock:  # !RELEASE! !ACQUIRE! foolock
            pass


class Bar:
    def __init__(self, lock_class: LockClassType) -> None:
        self.foo: Foo = Foo(lock_class)

    def bar(self) -> None:
        self.foo.foo()


@pytest.mark.parametrize(
    "collector_class,expected_repr",
    [
        (
            ThreadingLockCollector,
            "ThreadingLockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
            "capture_pct=1.0, nframes=64, "
            "endpoint_collection_enabled=True, tracer=None)",
        ),
        (
            ThreadingRLockCollector,
            "ThreadingRLockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
            "capture_pct=1.0, nframes=64, "
            "endpoint_collection_enabled=True, tracer=None)",
        ),
    ],
)
def test_repr(
    collector_class: CollectorClassType,
    expected_repr: str,
) -> None:
    test_collector._test_repr(collector_class, expected_repr)


@pytest.mark.parametrize(
    "lock_class,collector_class",
    [
        (threading.Lock, ThreadingLockCollector),
        (threading.RLock, ThreadingRLockCollector),
    ],
)
def test_patch(
    lock_class: LockClassType,
    collector_class: CollectorClassType,
) -> None:
    lock: LockClassType = lock_class
    collector: ThreadingLockCollector | ThreadingRLockCollector = collector_class()
    collector.start()
    assert lock == collector._original
    # wrapt makes this true
    assert lock == lock_class
    collector.stop()
    assert lock == lock_class
    assert collector._original == lock_class


@pytest.mark.subprocess(
    env=dict(WRAPT_DISABLE_EXTENSIONS="True", DD_PROFILING_FILE_PATH=__file__),
)
def test_wrapt_disable_extensions() -> None:
    import os
    import threading

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector import _lock
    from ddtrace.profiling.collector.threading import ThreadingLockCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import LineNo
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos
    from tests.profiling.collector.pprof_utils import pprof_pb2

    assert ddup.is_available, "ddup is not available"

    # Set up the ddup exporter
    test_name: str = "test_wrapt_disable_extensions"
    pprof_prefix: str = "/tmp" + os.sep + test_name
    output_filename: str = pprof_prefix + "." + str(os.getpid())
    ddup.config(
        env="test", service=test_name, version="my_version", output_filename=pprof_prefix
    )  # pyright: ignore[reportCallIssue]
    ddup.start()

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    # WRAPT_DISABLE_EXTENSIONS is a flag that can be set to disable the C extension
    # for wrapt. It's not set by default in dd-trace-py, but it can be set by
    # users. This test checks that the collector works even if the flag is set.
    assert os.environ.get("WRAPT_DISABLE_EXTENSIONS")
    assert _lock.WRAPT_C_EXT is False

    with ThreadingLockCollector(capture_pct=100):
        th_lock: threading.Lock = threading.Lock()  # !CREATE! test_wrapt_disable_extensions
        with th_lock:  # !ACQUIRE! !RELEASE! test_wrapt_disable_extensions
            pass

    ddup.upload()  # pyright: ignore[reportCallIssue]

    expected_filename: str = "test_threading.py"

    linenos: LineNo = get_lock_linenos("test_wrapt_disable_extensions", with_stmt=True)

    profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(output_filename)
    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="<module>",
                filename=expected_filename,
                linenos=linenos,
                lock_name="th_lock",
            )
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="<module>",
                filename=expected_filename,
                linenos=linenos,
                lock_name="th_lock",
            )
        ],
    )


# This test has to be run in a subprocess because it calls gevent.monkey.patch_all()
# which affects the whole process.
@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT"), reason="gevent is not available")
@pytest.mark.subprocess(
    env=dict(DD_PROFILING_FILE_PATH=__file__),
)
def test_lock_gevent_tasks() -> None:
    from gevent import monkey

    monkey.patch_all()

    import glob
    import os
    import threading

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.threading import ThreadingLockCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos

    assert ddup.is_available, "ddup is not available"

    # Set up the ddup exporter
    test_name: str = "test_lock_gevent_tasks"
    pprof_prefix: str = "/tmp" + os.sep + test_name
    output_filename: str = pprof_prefix + "." + str(os.getpid())
    ddup.config(
        env="test", service=test_name, version="my_version", output_filename=pprof_prefix
    )  # pyright: ignore[reportCallIssue]
    ddup.start()

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    def play_with_lock() -> None:
        lock: threading.Lock = threading.Lock()  # !CREATE! test_lock_gevent_tasks
        lock.acquire()  # !ACQUIRE! test_lock_gevent_tasks
        lock.release()  # !RELEASE! test_lock_gevent_tasks

    def validate_and_cleanup() -> None:
        ddup.upload()  # pyright: ignore[reportCallIssue]

        expected_filename: str = "test_threading.py"
        linenos: LineNo = get_lock_linenos(test_name)

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(
            output_filename
        )  # pyright: ignore[reportInvalidTypeForm]
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="play_with_lock",
                    filename=expected_filename,
                    linenos=linenos,
                    lock_name="lock",
                    # TODO: With stack_v2, the way we trace gevent greenlets has
                    # changed, and we'd need to expose an API to get the task_id,
                    # task_name, and task_frame.
                    # task_id=t.ident,
                    # task_name="foobar",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="play_with_lock",
                    filename=expected_filename,
                    linenos=linenos,
                    lock_name="lock",
                    # TODO: With stack_v2, the way we trace gevent greenlets has
                    # changed, and we'd need to expose an API to get the task_id,
                    # task_name, and task_frame.
                    # task_id=t.ident,
                    # task_name="foobar",
                ),
            ],
        )

        for f in glob.glob(pprof_prefix + ".*"):
            try:
                os.remove(f)
            except Exception as e:
                print("Error removing file: {}".format(e))

    with ThreadingLockCollector(capture_pct=100):
        t: threading.Thread = threading.Thread(name="foobar", target=play_with_lock)
        t.start()
        t.join()

    validate_and_cleanup()


# This test has to be run in a subprocess because it calls gevent.monkey.patch_all()
# which affects the whole process.
@pytest.mark.skipif(not os.getenv("DD_PROFILE_TEST_GEVENT"), reason="gevent is not available")
@pytest.mark.subprocess(
    env=dict(DD_PROFILING_FILE_PATH=__file__),
)
def test_rlock_gevent_tasks() -> None:
    from gevent import monkey

    monkey.patch_all()

    import glob
    import os
    import threading

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.threading import ThreadingRLockCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos

    assert ddup.is_available, "ddup is not available"

    # Set up the ddup exporter
    test_name: str = "test_rlock_gevent_tasks"
    pprof_prefix: str = "/tmp" + os.sep + test_name
    output_filename: str = pprof_prefix + "." + str(os.getpid())
    ddup.config(
        env="test", service=test_name, version="my_version", output_filename=pprof_prefix
    )  # pyright: ignore[reportCallIssue]
    ddup.start()

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    def play_with_lock() -> None:
        lock: threading.RLock = threading.RLock()  # !CREATE! test_rlock_gevent_tasks
        lock.acquire()  # !ACQUIRE! test_rlock_gevent_tasks
        lock.release()  # !RELEASE! test_rlock_gevent_tasks

    def validate_and_cleanup() -> None:
        ddup.upload()  # pyright: ignore[reportCallIssue]

        expected_filename: str = "test_threading.py"
        linenos: LineNo = get_lock_linenos(test_name)

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="play_with_lock",
                    filename=expected_filename,
                    linenos=linenos,
                    lock_name="lock",
                    # TODO: With stack_v2, the way we trace gevent greenlets has
                    # changed, and we'd need to expose an API to get the task_id,
                    # task_name, and task_frame.
                    # task_id=t.ident,
                    # task_name="foobar",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="play_with_lock",
                    filename=expected_filename,
                    linenos=linenos,
                    lock_name="lock",
                    # TODO: With stack_v2, the way we trace gevent greenlets has
                    # changed, and we'd need to expose an API to get the task_id,
                    # task_name, and task_frame.
                    # task_id=t.ident,
                    # task_name="foobar",
                ),
            ],
        )

        for f in glob.glob(pprof_prefix + ".*"):
            try:
                os.remove(f)
            except Exception as e:
                print("Error removing file: {}".format(e))

    with ThreadingRLockCollector(capture_pct=100):
        t: threading.Thread = threading.Thread(name="foobar", target=play_with_lock)
        t.start()
        t.join()

    validate_and_cleanup()


@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLE_ASSERTS="true"))
def test_assertion_error_raised_with_enable_asserts():
    """Ensure that AssertionError is propagated when config.enable_asserts=True."""
    import threading

    import mock
    import pytest

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.threading import ThreadingLockCollector

    # Initialize ddup (required before using collectors)
    assert ddup.is_available, "ddup is not available"
    ddup.config(env="test", service="test_asserts", version="1.0", output_filename="/tmp/test_asserts")
    ddup.start()

    with ThreadingLockCollector(capture_pct=100):
        lock = threading.Lock()

        # Patch _maybe_update_self_name to raise AssertionError
        lock._maybe_update_self_name = mock.Mock(side_effect=AssertionError("test: unexpected frame in stack"))

        with pytest.raises(AssertionError):
            # AssertionError should be propagated when enable_asserts=True
            lock.acquire()


@pytest.mark.subprocess(env=dict(DD_PROFILING_ENABLE_ASSERTS="false"))
def test_all_exceptions_suppressed_by_default() -> None:
    """
    Ensure that exceptions are silently suppressed in the `_acquire` method
    when config.enable_asserts=False (default).
    """
    import threading

    import mock

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.threading import ThreadingLockCollector

    # Initialize ddup (required before using collectors)
    assert ddup.is_available, "ddup is not available"
    ddup.config(env="test", service="test_exceptions", version="1.0", output_filename="/tmp/test_exceptions")
    ddup.start()

    with ThreadingLockCollector(capture_pct=100):
        lock = threading.Lock()

        # Patch _maybe_update_self_name to raise AssertionError
        lock._maybe_update_self_name = mock.Mock(side_effect=AssertionError("Unexpected frame in stack: 'fubar'"))
        lock.acquire()
        lock.release()

        # Patch _maybe_update_self_name to raise RuntimeError
        lock._maybe_update_self_name = mock.Mock(side_effect=RuntimeError("Some profiling error"))
        lock.acquire()
        lock.release()

        # Patch _maybe_update_self_name to raise Exception
        lock._maybe_update_self_name = mock.Mock(side_effect=Exception("Wut happened?!?!"))
        lock.acquire()
        lock.release()


class BaseThreadingLockCollectorTest:
    # These should be implemented by child classes
    @property
    def collector_class(self) -> CollectorClassType:
        raise NotImplementedError("Child classes must implement collector_class")

    @property
    def lock_class(self) -> LockClassType:
        raise NotImplementedError("Child classes must implement lock_class")

    # setup_method and teardown_method which will be called before and after
    # each test method, respectively, part of pytest api.
    def setup_method(self, method: Callable[..., None]) -> None:
        self.test_name: str = method.__name__
        self.pprof_prefix: str = "/tmp" + os.sep + self.test_name
        # The output filename will be /tmp/method_name.<pid>.<counter>.
        # The counter number is incremented for each test case, as the tests are
        # all run in a single process and share the same exporter.
        self.output_filename: str = self.pprof_prefix + "." + str(os.getpid())

        # ddup is available when the native module is compiled
        assert ddup.is_available, "ddup is not available"
        ddup.config(
            env="test", service=self.test_name, version="my_version", output_filename=self.pprof_prefix
        )  # pyright: ignore[reportCallIssue]
        ddup.start()

    def teardown_method(self, method: Callable[..., None]) -> None:
        # might be unnecessary but this will ensure that the file is removed
        # after each successful test, and when a test fails it's easier to
        # pinpoint and debug.
        for f in glob.glob(self.output_filename + ".*"):
            try:
                os.remove(f)
            except Exception as e:
                print("Error removing file: {}".format(e))

    def test_wrapper(self) -> None:
        collector: ThreadingLockCollector | ThreadingRLockCollector = self.collector_class()
        with collector:

            class Foobar(object):
                def __init__(self, lock_class: LockClassType) -> None:
                    lock: LockClassInst = lock_class()
                    assert lock.acquire()
                    lock.release()

            lock: LockClassInst = self.lock_class()
            assert lock.acquire()
            lock.release()

            # Try this way too
            Foobar(self.lock_class)

    def test_lock_events(self):
        # The first argument is the recorder.Recorder which is used for the
        # v1 exporter. We don't need it for the v2 exporter.
        with self.collector_class(capture_pct=100):
            lock = self.lock_class()  # !CREATE! test_lock_events
            lock.acquire()  # !ACQUIRE! test_lock_events
            lock.release()  # !RELEASE! test_lock_events
        # Calling upload will trigger the exporter to write to a file
        ddup.upload()

        profile = pprof_utils.parse_newest_profile(self.output_filename)
        linenos = get_lock_linenos("test_lock_events")
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="lock",
                ),
            ],
        )

    def test_lock_acquire_events_class(self) -> None:
        with self.collector_class(capture_pct=100):
            lock_class: LockClassType = self.lock_class  # Capture for inner class

            class Foobar(object):
                def lockfunc(self) -> None:
                    lock: LockClassInst = lock_class()  # !CREATE! test_lock_acquire_events_class
                    lock.acquire()  # !ACQUIRE! test_lock_acquire_events_class

            Foobar().lockfunc()

        ddup.upload()  # pyright: ignore[reportCallIssue]

        linenos: LineNo = get_lock_linenos("test_lock_acquire_events_class")

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="lockfunc",
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="lock",
                ),
            ],
        )

    def test_lock_events_tracer(self, tracer: Tracer) -> None:
        tracer._endpoint_call_counter_span_processor.enable()
        resource: str = str(uuid.uuid4())
        span_type: str = ext.SpanTypes.WEB
        with self.collector_class(
            tracer=tracer,
            capture_pct=100,
        ):
            lock1: LockClassInst = self.lock_class()  # !CREATE! test_lock_events_tracer_1
            lock1.acquire()  # !ACQUIRE! test_lock_events_tracer_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2: LockClassInst = self.lock_class()  # !CREATE! test_lock_events_tracer_2
                lock2.acquire()  # !ACQUIRE! test_lock_events_tracer_2
                lock1.release()  # !RELEASE! test_lock_events_tracer_1
                span_id: int = t.span_id

            lock2.release()  # !RELEASE! test_lock_events_tracer_2
        ddup.upload(tracer=tracer)

        linenos1: LineNo = get_lock_linenos("test_lock_events_tracer_1")
        linenos2: LineNo = get_lock_linenos("test_lock_events_tracer_2")

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                    span_id=span_id,
                    trace_endpoint=resource,
                    trace_type=span_type,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                    span_id=span_id,
                    trace_endpoint=resource,
                    trace_type=span_type,
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                ),
            ],
        )

    def test_lock_events_tracer_non_web(self, tracer: Tracer) -> None:
        tracer._endpoint_call_counter_span_processor.enable()
        resource: str = str(uuid.uuid4())
        span_type: str = ext.SpanTypes.SQL
        with self.collector_class(
            tracer=tracer,
            capture_pct=100,
        ):
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2: LockClassInst = self.lock_class()  # !CREATE! test_lock_events_tracer_non_web
                lock2.acquire()  # !ACQUIRE! test_lock_events_tracer_non_web
                span_id: int = t.span_id

            lock2.release()  # !RELEASE! test_lock_events_tracer_non_web
        ddup.upload(tracer=tracer)

        linenos2: LineNo = get_lock_linenos("test_lock_events_tracer_non_web")

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                    span_id=span_id,
                    # no trace endpoint for non-web spans
                    trace_type=span_type,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                ),
            ],
        )

    def test_lock_events_tracer_late_finish(self, tracer: Tracer) -> None:
        tracer._endpoint_call_counter_span_processor.enable()
        resource: str = str(uuid.uuid4())
        span_type: str = ext.SpanTypes.WEB
        with self.collector_class(
            tracer=tracer,
            capture_pct=100,
        ):
            lock1: LockClassInst = self.lock_class()  # !CREATE! test_lock_events_tracer_late_finish_1
            lock1.acquire()  # !ACQUIRE! test_lock_events_tracer_late_finish_1
            span: Span = tracer.start_span(name="test", span_type=span_type)  # pyright: ignore[reportCallIssue]
            lock2: LockClassInst = self.lock_class()  # !CREATE! test_lock_events_tracer_late_finish_2
            lock2.acquire()  # !ACQUIRE! test_lock_events_tracer_late_finish_2
            lock1.release()  # !RELEASE! test_lock_events_tracer_late_finish_1
            lock2.release()  # !RELEASE! test_lock_events_tracer_late_finish_2
        span.resource = resource
        span.finish()
        ddup.upload(tracer=tracer)

        linenos1: LineNo = get_lock_linenos("test_lock_events_tracer_late_finish_1")
        linenos2: LineNo = get_lock_linenos("test_lock_events_tracer_late_finish_2")

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                ),
            ],
        )

    def test_resource_not_collected(self, tracer: Tracer) -> None:
        tracer._endpoint_call_counter_span_processor.enable()
        resource: str = str(uuid.uuid4())
        span_type: str = ext.SpanTypes.WEB
        with self.collector_class(
            tracer=tracer,
            capture_pct=100,
            endpoint_collection_enabled=False,
        ):
            lock1: LockClassInst = self.lock_class()  # !CREATE! test_resource_not_collected_1
            lock1.acquire()  # !ACQUIRE! test_resource_not_collected_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2: LockClassInst = self.lock_class()  # !CREATE! test_resource_not_collected_2
                lock2.acquire()  # !ACQUIRE! test_resource_not_collected_2
                lock1.release()  # !RELEASE! test_resource_not_collected_1
                span_id: int = t.span_id
            lock2.release()  # !RELEASE! test_resource_not_collected_2
        ddup.upload(tracer=tracer)

        linenos1: LineNo = get_lock_linenos("test_resource_not_collected_1")
        linenos2: LineNo = get_lock_linenos("test_resource_not_collected_2")

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                    span_id=span_id,
                    trace_endpoint=None,
                    trace_type=span_type,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos1,
                    lock_name="lock1",
                    span_id=span_id,
                    trace_endpoint=None,
                    trace_type=span_type,
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos2,
                    lock_name="lock2",
                ),
            ],
        )

    def test_lock_enter_exit_events(self) -> None:
        with self.collector_class(capture_pct=100):
            th_lock: LockClassInst = self.lock_class()  # !CREATE! test_lock_enter_exit_events
            with th_lock:  # !ACQUIRE! !RELEASE! test_lock_enter_exit_events
                pass

        ddup.upload()  # pyright: ignore[reportCallIssue]

        # for enter/exits, we need to update the lock_linenos for versions >= 3.10
        linenos: LineNo = get_lock_linenos("test_lock_enter_exit_events", with_stmt=True)

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="th_lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="th_lock",
                ),
            ],
        )

    @pytest.mark.parametrize(
        "inspect_dir_enabled",
        [True, False],
    )
    def test_class_member_lock(self, inspect_dir_enabled: bool) -> None:
        with mock.patch("ddtrace.settings.profiling.config.lock.name_inspect_dir", inspect_dir_enabled):
            expected_lock_name: Optional[str] = "foo_lock" if inspect_dir_enabled else None

            with self.collector_class(capture_pct=100):
                foobar: Foo = Foo(self.lock_class)
                foobar.foo()
                bar: Bar = Bar(self.lock_class)
                bar.bar()

            ddup.upload()  # pyright: ignore[reportCallIssue]

            linenos: LineNo = get_lock_linenos("foolock", with_stmt=True)
            profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
            acquire_samples: List[pprof_pb2.Sample] = pprof_utils.get_samples_with_value_type(profile, "lock-acquire")
            assert len(acquire_samples) >= 2, "Expected at least 2 lock-acquire samples"
            release_samples: List[pprof_pb2.Sample] = pprof_utils.get_samples_with_value_type(profile, "lock-release")
            assert len(release_samples) >= 2, "Expected at least 2 lock-release samples"

            pprof_utils.assert_lock_events(
                profile,
                expected_acquire_events=[
                    pprof_utils.LockAcquireEvent(
                        caller_name="foo",
                        filename=os.path.basename(__file__),
                        linenos=linenos,
                        lock_name=expected_lock_name,
                    ),
                ],
                expected_release_events=[
                    pprof_utils.LockReleaseEvent(
                        caller_name="foo",
                        filename=os.path.basename(__file__),
                        linenos=linenos,
                        lock_name=expected_lock_name,
                    ),
                ],
            )

    def test_private_lock(self) -> None:
        class Foo:
            def __init__(self, lock_class: LockClassType) -> None:
                self.__lock: LockClassInst = lock_class()  # !CREATE! test_private_lock

            def foo(self) -> None:
                with self.__lock:  # !RELEASE! !ACQUIRE! test_private_lock
                    pass

        with self.collector_class(capture_pct=100):
            foo: Foo = Foo(self.lock_class)
            foo.foo()

        ddup.upload()  # pyright: ignore[reportCallIssue]

        linenos: LineNo = get_lock_linenos("test_private_lock", with_stmt=True)

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)

        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="foo",
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="_Foo__lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="foo",
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="_Foo__lock",
                ),
            ],
        )

    def test_inner_lock(self) -> None:
        class Bar:
            def __init__(self, lock_class: LockClassType) -> None:
                self.foo: Foo = Foo(lock_class)

            def bar(self) -> None:
                with self.foo.foo_lock:  # !RELEASE! !ACQUIRE! test_inner_lock
                    pass

        with self.collector_class(capture_pct=100):
            bar: Bar = Bar(self.lock_class)
            bar.bar()

        ddup.upload()  # pyright: ignore[reportCallIssue]

        linenos_foo: LineNo = get_lock_linenos("foolock")
        linenos_bar: LineNo = get_lock_linenos("test_inner_lock", with_stmt=True)
        linenos_bar = linenos_bar._replace(
            create=linenos_foo.create,
        )

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="bar",
                    filename=os.path.basename(__file__),
                    linenos=linenos_bar,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="bar",
                    filename=os.path.basename(__file__),
                    linenos=linenos_bar,
                ),
            ],
        )

    def test_anonymous_lock(self) -> None:
        with self.collector_class(capture_pct=100):
            with self.lock_class():  # !CREATE! !ACQUIRE! !RELEASE! test_anonymous_lock
                pass
        ddup.upload()  # pyright: ignore[reportCallIssue]

        linenos: LineNo = get_lock_linenos("test_anonymous_lock", with_stmt=True)

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                ),
            ],
        )

    def test_global_locks(self) -> None:
        global _test_global_lock, _test_global_bar_instance

        with self.collector_class(capture_pct=100):
            # Create true module-level globals
            _test_global_lock = self.lock_class()  # !CREATE! _test_global_lock

            class TestBar:
                def __init__(self, lock_class: LockClassType) -> None:
                    self.bar_lock: LockClassInst = lock_class()  # !CREATE! bar_lock

                def bar(self) -> None:
                    with self.bar_lock:  # !ACQUIRE! !RELEASE! bar_lock
                        pass

            def foo() -> None:
                global _test_global_lock
                assert _test_global_lock is not None
                with _test_global_lock:  # !ACQUIRE! !RELEASE! _test_global_lock
                    pass

            _test_global_bar_instance = TestBar(self.lock_class)

            # Use the locks
            foo()
            _test_global_bar_instance.bar()

        ddup.upload()  # pyright: ignore[reportCallIssue]

        # Process this file to get the correct line numbers for our !CREATE! comments
        init_linenos(__file__)

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        linenos_global: LineNo = get_lock_linenos("_test_global_lock", with_stmt=True)
        linenos_bar: LineNo = get_lock_linenos("bar_lock", with_stmt=True)

        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="foo",
                    filename=os.path.basename(__file__),
                    linenos=linenos_global,
                    lock_name="_test_global_lock",
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name="bar",
                    filename=os.path.basename(__file__),
                    linenos=linenos_bar,
                    lock_name="bar_lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="foo",
                    filename=os.path.basename(__file__),
                    linenos=linenos_global,
                    lock_name="_test_global_lock",
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name="bar",
                    filename=os.path.basename(__file__),
                    linenos=linenos_bar,
                    lock_name="bar_lock",
                ),
            ],
        )

    def test_upload_resets_profile(self) -> None:
        # This test checks that the profile is cleared after each upload() call
        # It is added in test_threading.py as LockCollector can easily be
        # configured to be deterministic with capture_pct=100.
        with self.collector_class(capture_pct=100):
            with self.lock_class():  # !CREATE! !ACQUIRE! !RELEASE! test_upload_resets_profile
                pass
        ddup.upload()  # pyright: ignore[reportCallIssue]

        linenos: LineNo = get_lock_linenos("test_upload_resets_profile", with_stmt=True)

        pprof: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        pprof_utils.assert_lock_events(
            pprof,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                ),
            ],
        )

        # Now we call upload() again, and we expect the profile to be empty
        ddup.upload()  # pyright: ignore[reportCallIssue]
        # parse_newest_profile raises an AssertionError if the profile doesn't
        # have any samples
        with pytest.raises(AssertionError):
            pprof_utils.parse_newest_profile(self.output_filename)


class TestThreadingLockCollector(BaseThreadingLockCollectorTest):
    """Test Lock profiling"""

    @property
    def collector_class(self) -> Type[ThreadingLockCollector]:
        return ThreadingLockCollector

    @property
    def lock_class(self) -> Type[threading.Lock]:
        return threading.Lock


class TestThreadingRLockCollector(BaseThreadingLockCollectorTest):
    """Test RLock profiling"""

    @property
    def collector_class(self) -> Type[ThreadingRLockCollector]:
        return ThreadingRLockCollector

    @property
    def lock_class(self) -> Type[threading.RLock]:
        return threading.RLock
