from __future__ import absolute_import

import _thread
import glob
import os
import sys
import threading
import time
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
from ddtrace.profiling.collector.threading import ThreadingBoundedSemaphoreCollector
from ddtrace.profiling.collector.threading import ThreadingLockCollector
from ddtrace.profiling.collector.threading import ThreadingRLockCollector
from ddtrace.profiling.collector.threading import ThreadingSemaphoreCollector
from tests.profiling.collector import pprof_utils
from tests.profiling.collector import test_collector
from tests.profiling.collector.lock_utils import LineNo
from tests.profiling.collector.lock_utils import get_lock_linenos
from tests.profiling.collector.lock_utils import init_linenos
from tests.profiling.collector.pprof_utils import pprof_pb2


PY_311_OR_ABOVE = sys.version_info[:2] >= (3, 11)


# Type aliases for supported classes
LockTypeClass = Union[
    Type[threading.Lock], Type[threading.RLock], Type[threading.Semaphore], Type[threading.BoundedSemaphore]
]
# threading.Lock and threading.RLock are factory functions that return _thread types.
# We reference the underlying _thread types directly to avoid creating instances at import time.
# threading.Semaphore and threading.BoundedSemaphore are Python classes, not factory functions.
LockTypeInst = Union[_thread.LockType, _thread.RLock, threading.Semaphore, threading.BoundedSemaphore]

CollectorTypeClass = Union[
    Type[ThreadingLockCollector],
    Type[ThreadingRLockCollector],
    Type[ThreadingSemaphoreCollector],
    Type[ThreadingBoundedSemaphoreCollector],
]
# Type alias for collector instances
CollectorTypeInst = Union[
    ThreadingLockCollector, ThreadingRLockCollector, ThreadingSemaphoreCollector, ThreadingBoundedSemaphoreCollector
]


# Module-level globals for testing global lock profiling
_test_global_lock: LockTypeInst


class TestBar: ...


_test_global_bar_instance: TestBar

init_linenos(__file__)


# Helper classes for testing lock collector
class Foo:
    def __init__(self, lock_class: LockTypeClass) -> None:
        self.foo_lock: LockTypeInst = lock_class()  # !CREATE! foolock

    def foo(self) -> None:
        with self.foo_lock:  # !RELEASE! !ACQUIRE! foolock
            pass


class Bar:
    def __init__(self, lock_class: LockTypeClass) -> None:
        self.foo: Foo = Foo(lock_class)

    def bar(self) -> None:
        self.foo.foo()


@pytest.mark.parametrize(
    "collector_class,expected_repr",
    [
        (
            ThreadingLockCollector,
            "ThreadingLockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, capture_pct=1.0, nframes=64, tracer=None)",  # noqa: E501
        ),
        (
            ThreadingRLockCollector,
            "ThreadingRLockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, capture_pct=1.0, nframes=64, tracer=None)",  # noqa: E501
        ),
        (
            ThreadingSemaphoreCollector,
            "ThreadingSemaphoreCollector(status=<ServiceStatus.STOPPED: 'stopped'>, capture_pct=1.0, nframes=64, tracer=None)",  # noqa: E501
        ),
        (
            ThreadingBoundedSemaphoreCollector,
            "ThreadingBoundedSemaphoreCollector(status=<ServiceStatus.STOPPED: 'stopped'>, capture_pct=1.0, nframes=64, tracer=None)",  # noqa: E501
        ),
    ],
)
def test_collector_repr(
    collector_class: CollectorTypeClass,
    expected_repr: str,
) -> None:
    test_collector._test_repr(collector_class, expected_repr)


@pytest.mark.parametrize(
    "collector_class,lock_class_name,expected_pattern",
    [
        (
            ThreadingLockCollector,
            "Lock",
            r"<_ProfiledLock\(<unlocked _thread\.lock object at 0x[0-9a-f]+>\) at test_threading\.py:{lineno}>",
        ),
        (
            ThreadingRLockCollector,
            "RLock",
            r"<_ProfiledLock\(<unlocked _thread\.RLock object owner=0 count=0 at 0x[0-9a-f]+>\) at test_threading\.py:{lineno}>",  # noqa: E501
        ),
        (
            ThreadingSemaphoreCollector,
            "Semaphore",
            # Multiple possible formats across Python versions:
            # 1. <unlocked _thread.Semaphore object owner=0 count=0 at 0x...> (pre-3.10)
            # 2. <threading.Semaphore object at 0x...> (3.10-3.12)
            # 3. <threading.Semaphore at 0x...: value=1> (3.13+)
            r"<_ProfiledLock\((<unlocked _thread\.Semaphore object owner=0 count=0 at 0x[0-9a-f]+>|<threading\.Semaphore( object)? at 0x[0-9a-f]+(: value=\d+)?>)\) at test_threading\.py:{lineno}>",  # noqa: E501
        ),
        (
            ThreadingBoundedSemaphoreCollector,
            "BoundedSemaphore",
            # Multiple possible formats across Python versions:
            # 1. <unlocked _thread.BoundedSemaphore object owner=0 count=0 at 0x...> (pre-3.10)
            # 2. <threading.BoundedSemaphore object at 0x...> (3.10-3.12)
            # 3. <threading.BoundedSemaphore at 0x...: value=1/1> (3.13+)
            r"<_ProfiledLock\((<unlocked _thread\.BoundedSemaphore object owner=0 count=0 at 0x[0-9a-f]+>|<threading\.BoundedSemaphore( object)? at 0x[0-9a-f]+(: value=\d+/\d+)?>)\) at test_threading\.py:{lineno}>",  # noqa: E501
        ),
    ],
)
def test_lock_repr(
    collector_class: CollectorTypeClass,
    lock_class_name: str,
    expected_pattern: str,
) -> None:
    """Test that __repr__ shows correct format with profiling info."""
    import re

    collector: CollectorTypeInst = collector_class(capture_pct=100)
    collector.start()
    try:
        # Get the lock class from threading module AFTER patching
        lock_class: LockTypeClass = getattr(threading, lock_class_name)
        lock: LockTypeInst = lock_class()  # !CREATE! test_lock_repr
    finally:
        collector.stop()

    repr_str: str = repr(lock)
    linenos: LineNo = get_lock_linenos("test_lock_repr")
    pattern: str = expected_pattern.format(lineno=linenos.create)
    assert re.match(pattern, repr_str), f"repr {repr_str!r} didn't match pattern {pattern!r}"


def test_patch():
    from ddtrace.profiling.collector._lock import _LockAllocatorWrapper

    lock = threading.Lock
    collector = ThreadingLockCollector()
    collector.start()
    assert lock == collector._original_lock
    # After patching, threading.Lock is replaced with our wrapper
    # The old reference (lock) points to the original builtin Lock class
    assert lock != threading.Lock  # They're different after patching
    assert isinstance(threading.Lock, _LockAllocatorWrapper)  # threading.Lock is now wrapped
    assert callable(threading.Lock)  # and it's callable
    collector.stop()
    # After stopping, everything is restored
    assert lock == threading.Lock
    assert collector._original_lock == threading.Lock


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="only works on linux")
@pytest.mark.subprocess(err=None)
# For macOS: Could print 'Error uploading' but okay to ignore since we are checking if native_id is set
def test_user_threads_have_native_id():
    from os import getpid
    from threading import Thread
    from threading import _MainThread
    from threading import current_thread
    from time import sleep

    from ddtrace.profiling import profiler

    p = profiler.Profiler()
    p.start()

    main = current_thread()
    assert isinstance(main, _MainThread)
    # We expect the current thread to have the same ID as the PID
    assert main.native_id == getpid(), (main.native_id, getpid())

    t = Thread(target=lambda: None)
    t.start()

    for _ in range(10):
        try:
            # The TID should be higher than the PID, but not too high
            assert 0 < t.native_id - getpid() < 100, (t.native_id, getpid())
        except AttributeError:
            # The native_id attribute is set by the thread so we might have to
            # wait a bit for it to be set.
            sleep(0.1)
        else:
            break
    else:
        raise AssertionError("Thread.native_id not set")

    t.join()

    p.stop()


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
        env="test",
        service=test_name,
        version="my_version",
        output_filename=pprof_prefix,
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

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(output_filename)  # pyright: ignore[reportInvalidTypeForm]
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="play_with_lock",
                    filename=expected_filename,
                    linenos=linenos,
                    lock_name="lock",
                    # TODO: With stack, the way we trace gevent greenlets has
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
                    # TODO: With stack, the way we trace gevent greenlets has
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
        env="test",
        service=test_name,
        version="my_version",
        output_filename=pprof_prefix,
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
                    # TODO: With stack, the way we trace gevent greenlets has
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
                    # TODO: With stack, the way we trace gevent greenlets has
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

    from ddtrace.profiling.collector.threading import ThreadingLockCollector
    from tests.profiling.collector.test_utils import init_ddup

    init_ddup("test_asserts")

    with ThreadingLockCollector(capture_pct=100):
        lock = threading.Lock()

        # Patch _update_name to raise AssertionError
        lock._update_name = mock.Mock(side_effect=AssertionError("test: unexpected frame in stack"))

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

    from ddtrace.profiling.collector.threading import ThreadingLockCollector
    from tests.profiling.collector.test_utils import init_ddup

    init_ddup("test_exceptions")

    with ThreadingLockCollector(capture_pct=100):
        lock = threading.Lock()

        # Patch _update_name to raise AssertionError
        lock._update_name = mock.Mock(side_effect=AssertionError("Unexpected frame in stack: 'fubar'"))
        lock.acquire()
        lock.release()

        # Patch _update_name to raise RuntimeError
        lock._update_name = mock.Mock(side_effect=RuntimeError("Some profiling error"))
        lock.acquire()
        lock.release()

        # Patch _update_name to raise Exception
        lock._update_name = mock.Mock(side_effect=Exception("Wut happened?!?!"))
        lock.acquire()
        lock.release()


def test_semaphore_and_bounded_semaphore_collectors_coexist() -> None:
    """Test that Semaphore and BoundedSemaphore collectors can run simultaneously.

    Tests proper patching where inheritance is involved if both parent and child classes are patched,
    e.g. when BoundedSemaphore's c-tor calls Semaphore c-tor.
    We expect that the call to Semaphore c-tor goes to the unpatched version, and NOT our patched version.
    """
    from tests.profiling.collector.test_utils import init_ddup

    init_ddup("test_semaphore_and_bounded_semaphore_collectors_coexist")

    # Both collectors active at the same time - this triggers the inheritance case
    with ThreadingSemaphoreCollector(capture_pct=100), ThreadingBoundedSemaphoreCollector(capture_pct=100):
        sem = threading.Semaphore(2)
        sem.acquire()
        sem.release()

        bsem = threading.BoundedSemaphore(3)
        bsem.acquire()
        bsem.release()

        # If inheritance delegation failed, these attributes will be missing.
        wrapped_bsem = bsem.__wrapped__
        assert hasattr(wrapped_bsem, "_cond"), "BoundedSemaphore._cond not initialized (inheritance bug)"
        assert hasattr(wrapped_bsem, "_value"), "BoundedSemaphore._value not initialized (inheritance bug)"
        assert hasattr(wrapped_bsem, "_initial_value"), "BoundedSemaphore._initial_value not initialized"

        # Verify BoundedSemaphore behavior is preserved (i.e. it raises on over-release)
        bsem2 = threading.BoundedSemaphore(1)
        bsem2.acquire()
        bsem2.release()
        with pytest.raises(ValueError, match="Semaphore released too many times"):
            bsem2.release()


class BaseThreadingLockCollectorTest:
    # These should be implemented by child classes
    @property
    def collector_class(self) -> CollectorTypeClass:
        raise NotImplementedError("Child classes must implement collector_class")

    @property
    def lock_class(self) -> LockTypeClass:
        raise NotImplementedError("Child classes must implement lock_class")

    # setup_method and teardown_method which will be called before and after
    # each test method, respectively, part of pytest api.
    def setup_method(self, method: Callable[..., None]) -> None:
        self.test_name: str = method.__qualname__ if PY_311_OR_ABOVE else method.__name__
        self.pprof_prefix: str = "/tmp" + os.sep + self.test_name
        # The output filename will be /tmp/method_name.<pid>.<counter>.
        # The counter number is incremented for each test case, as the tests are
        # all run in a single process and share the same exporter.
        self.output_filename: str = self.pprof_prefix + "." + str(os.getpid())

        # ddup is available when the native module is compiled
        assert ddup.is_available, "ddup is not available"
        ddup.config(
            env="test",
            service=self.test_name,
            version="my_version",
            output_filename=self.pprof_prefix,
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
        with self.collector_class():

            class Foobar(object):
                def __init__(self, lock_class: LockTypeClass) -> None:
                    lock: LockTypeInst = lock_class()
                    assert lock.acquire()
                    lock.release()

            lock: LockTypeInst = self.lock_class()
            assert lock.acquire()
            lock.release()

            # Try this way too
            Foobar(self.lock_class)

    def test_lock_events(self) -> None:
        # The first argument is the recorder.Recorder which is used for the
        # v1 exporter. We don't need it for the v2 exporter.
        with self.collector_class(capture_pct=100):
            lock: LockTypeInst = self.lock_class()  # !CREATE! test_lock_events
            lock.acquire()  # !ACQUIRE! test_lock_events
            lock.release()  # !RELEASE! test_lock_events
        # Calling upload will trigger the exporter to write to a file
        ddup.upload()

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        linenos: LineNo = get_lock_linenos("test_lock_events")
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
        # Store reference to class for later qualname access
        foobar_class: Optional[Type] = None

        with self.collector_class(capture_pct=100):
            lock_class: LockTypeClass = self.lock_class  # Capture for inner class

            class Foobar(object):
                def lockfunc(self) -> None:
                    lock: LockTypeInst = lock_class()  # !CREATE! test_lock_acquire_events_class
                    lock.acquire()  # !ACQUIRE! test_lock_acquire_events_class

            # Capture reference before context manager exits
            foobar_class = Foobar
            Foobar().lockfunc()

        ddup.upload()  # pyright: ignore[reportCallIssue]

        linenos: LineNo = get_lock_linenos("test_lock_acquire_events_class")

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)
        assert foobar_class is not None
        caller_name = foobar_class.lockfunc.__qualname__ if PY_311_OR_ABOVE else foobar_class.lockfunc.__name__
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=caller_name,
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
            lock1: LockTypeInst = self.lock_class()  # !CREATE! test_lock_events_tracer_1
            lock1.acquire()  # !ACQUIRE! test_lock_events_tracer_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2: LockTypeInst = self.lock_class()  # !CREATE! test_lock_events_tracer_2
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
                lock2: LockTypeInst = self.lock_class()  # !CREATE! test_lock_events_tracer_non_web
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
            lock1: LockTypeInst = self.lock_class()  # !CREATE! test_lock_events_tracer_late_finish_1
            lock1.acquire()  # !ACQUIRE! test_lock_events_tracer_late_finish_1
            span: Span = tracer.start_span(name="test", span_type=span_type)  # pyright: ignore[reportCallIssue]
            lock2: LockTypeInst = self.lock_class()  # !CREATE! test_lock_events_tracer_late_finish_2
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
        ):
            lock1: LockTypeInst = self.lock_class()  # !CREATE! test_resource_not_collected_1
            lock1.acquire()  # !ACQUIRE! test_resource_not_collected_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2: LockTypeInst = self.lock_class()  # !CREATE! test_resource_not_collected_2
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
            th_lock: LockTypeInst = self.lock_class()  # !CREATE! test_lock_enter_exit_events
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
        with mock.patch(
            "ddtrace.internal.settings.profiling.config.lock.name_inspect_dir",
            inspect_dir_enabled,
        ):
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

            caller_name = Foo.foo.__qualname__ if PY_311_OR_ABOVE else Foo.foo.__name__

            pprof_utils.assert_lock_events(
                profile,
                expected_acquire_events=[
                    pprof_utils.LockAcquireEvent(
                        caller_name=caller_name,
                        filename=os.path.basename(__file__),
                        linenos=linenos,
                        lock_name=expected_lock_name,
                    ),
                ],
                expected_release_events=[
                    pprof_utils.LockReleaseEvent(
                        caller_name=caller_name,
                        filename=os.path.basename(__file__),
                        linenos=linenos,
                        lock_name=expected_lock_name,
                    ),
                ],
            )

    def test_private_lock(self) -> None:
        # Store reference to class for later qualname access
        foo_class: Optional[Type] = None

        class Foo:
            def __init__(self, lock_class: LockTypeClass) -> None:
                self.__lock: LockTypeInst = lock_class()  # !CREATE! test_private_lock

            def foo(self) -> None:
                with self.__lock:  # !RELEASE! !ACQUIRE! test_private_lock
                    pass

        # Capture reference before context manager
        foo_class = Foo

        with self.collector_class(capture_pct=100):
            foo: Foo = Foo(self.lock_class)
            foo.foo()

        ddup.upload()  # pyright: ignore[reportCallIssue]

        linenos: LineNo = get_lock_linenos("test_private_lock", with_stmt=True)

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)

        assert foo_class is not None
        caller_name = foo_class.foo.__qualname__ if PY_311_OR_ABOVE else foo_class.foo.__name__
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=caller_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="_Foo__lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=caller_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="_Foo__lock",
                ),
            ],
        )

    def test_inner_lock(self) -> None:
        # Store reference to class for later qualname access
        bar_class: Optional[Type] = None

        class Bar:
            def __init__(self, lock_class: LockTypeClass) -> None:
                self.foo: Foo = Foo(lock_class)

            def bar(self) -> None:
                with self.foo.foo_lock:  # !RELEASE! !ACQUIRE! test_inner_lock
                    pass

        # Capture reference before context manager
        bar_class = Bar

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
        assert bar_class is not None
        caller_name = bar_class.bar.__qualname__ if PY_311_OR_ABOVE else bar_class.bar.__name__
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=caller_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos_bar,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=caller_name,
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

        # Store references to functions/classes for later qualname access
        foo_func: Optional[Callable[[], None]] = None
        test_bar_class: Optional[Type] = None

        with self.collector_class(capture_pct=100):
            # Create true module-level globals
            _test_global_lock = self.lock_class()  # !CREATE! _test_global_lock

            class TestBar:
                def __init__(self, lock_class: LockTypeClass) -> None:
                    self.bar_lock: LockTypeInst = lock_class()  # !CREATE! bar_lock

                def bar(self) -> None:
                    with self.bar_lock:  # !ACQUIRE! !RELEASE! bar_lock
                        pass

            def foo() -> None:
                global _test_global_lock
                assert _test_global_lock is not None
                with _test_global_lock:  # !ACQUIRE! !RELEASE! _test_global_lock
                    pass

            # Capture references before context manager exits
            foo_func = foo
            test_bar_class = TestBar

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

        assert foo_func is not None
        assert test_bar_class is not None
        caller_name_foo = foo_func.__qualname__ if PY_311_OR_ABOVE else foo_func.__name__
        caller_name_bar = test_bar_class.bar.__qualname__ if PY_311_OR_ABOVE else test_bar_class.bar.__name__

        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=caller_name_foo,
                    filename=os.path.basename(__file__),
                    linenos=linenos_global,
                    lock_name="_test_global_lock",
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name=caller_name_bar,
                    filename=os.path.basename(__file__),
                    linenos=linenos_bar,
                    lock_name="bar_lock",
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=caller_name_foo,
                    filename=os.path.basename(__file__),
                    linenos=linenos_global,
                    lock_name="_test_global_lock",
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name=caller_name_bar,
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
        num_files_before_second_upload: int = len(glob.glob(self.output_filename + ".*.pprof"))

        ddup.upload()  # pyright: ignore[reportCallIssue]

        num_files_after_second_upload: int = len(glob.glob(self.output_filename + ".*.pprof"))

        # A new profile file should always be created (upload_seq increments)
        assert num_files_after_second_upload - num_files_before_second_upload == 1, (
            f"Expected 1 new file, got {num_files_after_second_upload - num_files_before_second_upload}."
        )

        # The newest profile file should be empty (no samples), which causes an AssertionError
        with pytest.raises(AssertionError, match="No samples found in profile"):
            pprof_utils.parse_newest_profile(self.output_filename)

    def test_lock_hash(self) -> None:
        """Test that __hash__ allows profiled locks to be used in sets and dicts."""
        with self.collector_class(capture_pct=100):
            lock1: LockTypeInst = self.lock_class()
            lock2: LockTypeInst = self.lock_class()

            # Different locks should have different hashes
            assert hash(lock1) != hash(lock2)

            # Same lock should have consistent hash
            assert hash(lock1) == hash(lock1)

            # Should be usable in a set
            lock_set: set[LockTypeInst] = {lock1, lock2}
            assert len(lock_set) == 2
            assert lock1 in lock_set
            assert lock2 in lock_set

            # Should be usable as dict keys
            lock_dict: dict[LockTypeInst, str] = {lock1: "first", lock2: "second"}
            assert lock_dict[lock1] == "first"
            assert lock_dict[lock2] == "second"

    def test_lock_equality(self) -> None:
        """Test that __eq__ compares locks correctly."""
        with self.collector_class(capture_pct=100):
            lock1: LockTypeInst = self.lock_class()
            lock2: LockTypeInst = self.lock_class()

            # Different locks should not be equal
            assert lock1 != lock2

            # Same lock should be equal to itself
            assert lock1 == lock1

            # A profiled lock should be comparable with its wrapped lock
            from ddtrace.profiling.collector._lock import _ProfiledLock

            assert isinstance(lock1, _ProfiledLock)
            # The wrapped lock can be accessed via __wrapped__ attribute
            # Note: We access it directly as a public attribute, not via name mangling
            wrapped: object = lock1.__wrapped__
            assert lock1 == wrapped
            assert wrapped == lock1

    def test_lock_getattr_nonexistent(self) -> None:
        """Test that __getattr__ raises AttributeError for non-existent attributes."""
        with self.collector_class(capture_pct=100):
            lock: LockTypeInst = self.lock_class()
            with pytest.raises(AttributeError):
                _ = lock.this_attribute_does_not_exist  # type: ignore[attr-defined]

    def test_lock_slots_enforced(self) -> None:
        """Test that __slots__ is defined on _ProfiledLock for memory efficiency."""
        with self.collector_class(capture_pct=100):
            lock: LockTypeInst = self.lock_class()
            from ddtrace.profiling.collector._lock import _ProfiledLock

            assert isinstance(lock, _ProfiledLock)
            # Verify __slots__ is defined on the base class (for memory efficiency)
            assert hasattr(_ProfiledLock, "__slots__")
            # Verify all expected attributes are in __slots__
            expected_slots: set[str] = {
                "__wrapped__",
                "tracer",
                "max_nframes",
                "capture_sampler",
                "init_location",
                "acquired_time",
                "name",
                "is_internal",
            }
            assert set(_ProfiledLock.__slots__) == expected_slots

    def test_lock_profiling_overhead_reasonable(self) -> None:
        """Test that profiling overhead with 0% capture is bounded."""
        # Measure without profiling (collector stopped)
        regular_lock: LockTypeInst = self.lock_class()
        start: float = time.perf_counter()
        iterations: int = 10000  # More iterations for stable measurement
        for _ in range(iterations):
            regular_lock.acquire()
            regular_lock.release()
        regular_time: float = time.perf_counter() - start

        # Measure with profiling at 0% capture (should skip profiling logic)
        with self.collector_class(capture_pct=0):
            profiled_lock: LockTypeInst = self.lock_class()
            start = time.perf_counter()
            for _ in range(iterations):
                profiled_lock.acquire()
                profiled_lock.release()
            profiled_time_zero: float = time.perf_counter() - start

        # With 0% capture, there's still wrapper overhead but should be reasonable
        # This is a smoke test to catch egregious performance issues, not a precise benchmark
        # Allow up to 50x overhead since lock operations are extremely fast (microseconds)
        # and wrapper overhead is constant per call
        overhead_multiplier: float = profiled_time_zero / regular_time if regular_time > 0 else 1
        assert overhead_multiplier < 50, (
            f"Overhead too high: {overhead_multiplier}x (regular: {regular_time:.6f}s, "
            f"profiled: {profiled_time_zero:.6f}s)"
        )  # noqa: E501

    def test_release_not_sampled_when_acquire_not_sampled(self) -> None:
        """Test that lock release events are NOT sampled if their corresponding acquire was not sampled."""
        # Use capture_pct=0 to ensure acquire is NEVER sampled
        with self.collector_class(capture_pct=0):
            lock: LockTypeInst = self.lock_class()
            # Do multiple acquire/release cycles
            for _ in range(10):
                lock.acquire()
                time.sleep(0.001)
                lock.release()

        ddup.upload()

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename, assert_samples=False)
        release_samples: List[pprof_pb2.Sample] = pprof_utils.get_samples_with_value_type(profile, "lock-release")

        # release samples should NOT be generated when acquire wasn't sampled
        assert len(release_samples) == 0, (
            f"Expected no release samples when acquire wasn't sampled, got {len(release_samples)}"
        )


class TestThreadingLockCollector(BaseThreadingLockCollectorTest):
    """Test Lock profiling"""

    @property
    def collector_class(self) -> Type[ThreadingLockCollector]:
        return ThreadingLockCollector

    @property
    def lock_class(self) -> Type[threading.Lock]:
        return threading.Lock

    def test_lock_getattr(self) -> None:
        """Test that __getattr__ delegates Lock-specific attributes."""
        with self.collector_class(capture_pct=100):
            lock: LockTypeInst = self.lock_class()
            from ddtrace.profiling.collector._lock import _ProfiledLock

            assert isinstance(lock, _ProfiledLock)

            # acquire_lock and release_lock are aliases that exist on _thread.lock
            # but are not explicitly defined on _ProfiledLock, so they should be delegated
            assert hasattr(lock, "acquire_lock")
            assert callable(lock.acquire_lock)
            assert "acquire_lock" not in _ProfiledLock.__dict__

            # Test that they work
            assert lock.acquire_lock()
            lock.release_lock()


class TestThreadingRLockCollector(BaseThreadingLockCollectorTest):
    """Test RLock profiling"""

    @property
    def collector_class(self) -> Type[ThreadingRLockCollector]:
        return ThreadingRLockCollector

    @property
    def lock_class(self) -> Type[threading.RLock]:
        return threading.RLock

    def test_lock_getattr(self) -> None:
        """Test that __getattr__ delegates RLock-specific attributes."""
        with self.collector_class(capture_pct=100):
            lock: LockTypeInst = self.lock_class()
            from ddtrace.profiling.collector._lock import _ProfiledLock

            assert isinstance(lock, _ProfiledLock)

            # _is_owned() is an RLock-specific method that should be delegated via __getattr__
            assert hasattr(lock, "_is_owned")
            assert callable(lock._is_owned)
            assert "_is_owned" not in _ProfiledLock.__dict__

            # Initially lock should not be owned
            assert not lock._is_owned()

            # After acquiring, it should be owned
            lock.acquire()
            assert lock._is_owned()

            # After releasing, it should not be owned
            lock.release()
            assert not lock._is_owned()


class BaseSemaphoreTest(BaseThreadingLockCollectorTest):
    """Base test class for Semaphore-like locks (Semaphore and BoundedSemaphore).

    Contains tests that apply to both regular Semaphore and BoundedSemaphore,
    particularly those related to internal lock detection and Condition-based implementation.
    """

    def _verify_no_double_counting(self, marker_name: str, lock_var_name: str) -> None:
        """Helper method to verify no double-counting in profile output.

        Args:
            marker_name: The marker name used in !CREATE! comments (e.g., "test_no_double_counting")
            lock_var_name: The lock variable name to check in profile (e.g., "sem")
        """
        ddup.upload()

        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)

        # Count lock events (we expect 1 and only 1 acquire / release pair of samples.)
        acquire_samples_count: int = len(pprof_utils.get_samples_with_value_type(profile, "lock-acquire"))
        release_samples_count: int = len(pprof_utils.get_samples_with_value_type(profile, "lock-release"))

        # Should have exactly 1 event!
        # 1 event = Semaphore-like lock profiled, internal Lock skipped (correct)
        # 2+ events = Both Semaphore-like AND internal Lock profiled (double-counting bug)
        lock_type_name: str = self.lock_class.__name__
        assert acquire_samples_count == 1, (
            f"Expected 1 acquire event ({lock_type_name} only), got {acquire_samples_count}."
        )
        assert release_samples_count == 1, (
            f"Expected 1 release event ({lock_type_name} only), got {release_samples_count}."
        )

        # Verify the single event is the Semaphore-like lock (not the internal Lock)
        linenos: LineNo = get_lock_linenos(marker_name)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name=lock_var_name,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name=lock_var_name,
                ),
            ],
        )

    def _verify_stack_trace_to_user_code(self, marker_name: str, lock_var_name: str) -> None:
        """Helper method to verify stack traces point to user code, not threading.py internals.

        Args:
            marker_name: The marker name used in !CREATE! comments
            lock_var_name: The lock variable name to check in profile
        """
        ddup.upload()

        linenos: LineNo = get_lock_linenos(marker_name)
        profile: pprof_pb2.Profile = pprof_utils.parse_newest_profile(self.output_filename)

        # stack traces should show test_threading.py (this file),
        # not threading.py (where Condition/Semaphore internals live)
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name=lock_var_name,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name=lock_var_name,
                ),
            ],
        )

    def test_internal_lock_marked_correctly(self) -> None:
        """Verify that locks created internally by threading.py are marked as internal (`self.is_internal == True`."""
        from ddtrace.profiling.collector.threading import ThreadingLockCollector

        lock_type_name: str = self.lock_class.__name__

        # Start Lock and Semaphore-like collectors to capture both lock types
        with ThreadingLockCollector(capture_pct=100), self.collector_class(capture_pct=100):
            # Create a regular lock from user code
            regular_lock: LockTypeInst = threading.Lock()
            assert hasattr(regular_lock, "is_internal"), "Lock should be wrapped with is_internal attribute"
            assert not regular_lock.is_internal, f"Regular lock should NOT be internal, got: {regular_lock.is_internal}"  # pyright: ignore[reportAttributeAccessIssue]

            # Create a semaphore-like lock - it should NOT be internal
            sem: LockTypeInst = self.lock_class(1)
            assert not sem.is_internal, f"{lock_type_name} should NOT be internal, got: {sem.is_internal}"  # pyright: ignore[reportAttributeAccessIssue]

            # Access the internal lock (Semaphore-like -> Condition -> Lock)
            # The Condition is at sem._cond, and its lock is at sem._cond._lock
            internal_lock: LockTypeInst = sem._cond._lock  # pyright: ignore[reportAttributeAccessIssue]
            assert hasattr(internal_lock, "is_internal"), "Internal lock should be wrapped"
            assert internal_lock.is_internal, (  # pyright: ignore[reportAttributeAccessIssue]
                "Lock created by threading.py (inside Condition) SHOULD be marked as internal."
            )

    def test_acquire_return_values_preserved(self) -> None:
        """Test that profiling wrapper preserves acquire() return values (transparency test).

        This verifies the wrapper doesn't break different acquire() modes.
        Both Semaphore and BoundedSemaphore have identical acquire() behavior.

        Note: We use capture_pct=0 because we only care about behavior, not profile output.
        """
        with self.collector_class(capture_pct=0):
            sem: LockTypeInst = self.lock_class(1)

            # Test that blocking acquire succeeds
            result1 = sem.acquire(blocking=True)
            assert result1 in (True, None), "Wrapper must preserve blocking acquire return value"

            # Test that non-blocking acquire on unavailable semaphore returns False
            result2 = sem.acquire(blocking=False)
            assert result2 is False, "Wrapper must preserve non-blocking acquire return value (False when unavailable)"

            sem.release()

            # Test that non-blocking acquire on available semaphore returns True
            result3 = sem.acquire(blocking=False)
            assert result3 is True, "Wrapper must preserve non-blocking acquire return value (True when available)"

            sem.release()


class TestThreadingSemaphoreCollector(BaseSemaphoreTest):
    """Test Semaphore profiling"""

    @property
    def collector_class(self) -> Type[ThreadingSemaphoreCollector]:
        return ThreadingSemaphoreCollector

    @property
    def lock_class(self) -> Type[threading.Semaphore]:
        return threading.Semaphore

    def test_stack_trace_points_to_user_code(self) -> None:
        """Verify Semaphore stack traces point to user code (uses Semaphore-specific markers)."""
        with self.collector_class(capture_pct=100):
            sem: LockTypeInst = self.lock_class(2)  # !CREATE! test_stack_trace_sem
            sem.acquire()  # !ACQUIRE! test_stack_trace_sem
            sem.release()  # !RELEASE! test_stack_trace_sem

        self._verify_stack_trace_to_user_code("test_stack_trace_sem", "sem")

    def test_no_double_counting_with_lock_collector(self) -> None:
        """Verify no double-counting with Semaphore (uses Semaphore-specific markers)."""
        from ddtrace.profiling.collector.threading import ThreadingLockCollector

        with ThreadingLockCollector(capture_pct=100), self.collector_class(capture_pct=100):
            sem: LockTypeInst = self.lock_class(1)  # !CREATE! test_no_double_counting
            sem.acquire()  # !ACQUIRE! test_no_double_counting
            sem.release()  # !RELEASE! test_no_double_counting

        self._verify_no_double_counting("test_no_double_counting", "sem")

    def test_unbounded_behavior_preserved(self) -> None:
        """Test that profiling wrapper preserves Semaphore's unbounded behavior (transparency test).

        Unlike BoundedSemaphore, regular Semaphore allows unlimited releases (no ValueError).
        This verifies our profiling wrapper preserves this behavior.

        Note: We use capture_pct=0 because we only care about behavior, not profile output.
        """
        with self.collector_class(capture_pct=0):
            sem: LockTypeInst = self.lock_class(1)

            # Acquire and release normally
            sem.acquire()
            sem.release()

            # Regular Semaphore allows releasing beyond initial value (no exception)
            # This is the key difference from BoundedSemaphore
            sem.release()  # Should NOT raise ValueError
            sem.release()  # Can keep releasing

            # Verify we can still acquire (value has increased)
            assert sem.acquire(blocking=False) is True, "Semaphore should allow acquire after extra releases"


class TestThreadingBoundedSemaphoreCollector(BaseSemaphoreTest):
    """Test BoundedSemaphore profiling"""

    @property
    def collector_class(self) -> Type[ThreadingBoundedSemaphoreCollector]:
        return ThreadingBoundedSemaphoreCollector

    @property
    def lock_class(self) -> Type[threading.BoundedSemaphore]:
        return threading.BoundedSemaphore

    def test_stack_trace_points_to_user_code(self) -> None:
        """Verify BoundedSemaphore stack traces point to user code (uses BoundedSemaphore-specific markers)."""
        with self.collector_class(capture_pct=100):
            bsem: LockTypeInst = self.lock_class(2)  # !CREATE! test_stack_trace_bsem
            bsem.acquire()  # !ACQUIRE! test_stack_trace_bsem
            bsem.release()  # !RELEASE! test_stack_trace_bsem

        self._verify_stack_trace_to_user_code("test_stack_trace_bsem", "bsem")

    def test_no_double_counting_with_lock_collector(self) -> None:
        """Verify no double-counting with BoundedSemaphore (uses BoundedSemaphore-specific markers)."""
        from ddtrace.profiling.collector.threading import ThreadingLockCollector

        with ThreadingLockCollector(capture_pct=100), self.collector_class(capture_pct=100):
            bsem: LockTypeInst = self.lock_class(1)  # !CREATE! test_no_double_counting_bounded
            bsem.acquire()  # !ACQUIRE! test_no_double_counting_bounded
            bsem.release()  # !RELEASE! test_no_double_counting_bounded

        self._verify_no_double_counting("test_no_double_counting_bounded", "bsem")

    def test_bounded_behavior_preserved(self) -> None:
        """Test that profiling wrapper preserves BoundedSemaphore's bounded behavior (transparency test).

        This verifies the wrapper doesn't interfere with BoundedSemaphore's unique characteristic:
        raising ValueError when releasing beyond the initial value.

        Note: We use capture_pct=0 because we only care about behavior, not profile output.
        """
        with self.collector_class(capture_pct=0):
            sem: LockTypeInst = self.lock_class(1)
            sem.acquire()
            sem.release()
            # BoundedSemaphore should raise ValueError when releasing more than initial value
            # This proves our profiling wrapper doesn't break BoundedSemaphore's behavior
            with pytest.raises(ValueError, match="Semaphore released too many times"):
                sem.release()
