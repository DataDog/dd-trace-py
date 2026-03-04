from __future__ import annotations

import _thread
import asyncio
import glob
import os
import sys
from typing import Any
from typing import Callable
from typing import Union
import uuid

import pytest

from ddtrace import ext
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector._lock import _LockAllocatorWrapper as LockAllocatorWrapper
from ddtrace.profiling.collector.asyncio import AsyncioBoundedSemaphoreCollector
from ddtrace.profiling.collector.asyncio import AsyncioConditionCollector
from ddtrace.profiling.collector.asyncio import AsyncioLockCollector
from ddtrace.profiling.collector.asyncio import AsyncioSemaphoreCollector
from tests.profiling.collector import pprof_utils
from tests.profiling.collector import test_collector
from tests.profiling.collector.lock_utils import get_lock_linenos
from tests.profiling.collector.lock_utils import init_linenos


init_linenos(__file__)

PY_311_OR_ABOVE = sys.version_info[:2] >= (3, 11)

# type aliases for supported classes
LockTypeInst = Union[asyncio.Lock, asyncio.Semaphore, asyncio.BoundedSemaphore, asyncio.Condition]
LockTypeClass = type[LockTypeInst]

CollectorTypeInst = Union[
    AsyncioLockCollector, AsyncioSemaphoreCollector, AsyncioBoundedSemaphoreCollector, AsyncioConditionCollector
]
CollectorTypeClass = type[CollectorTypeInst]


@pytest.mark.parametrize(
    "collector_class,expected_repr",
    [
        (
            AsyncioLockCollector,
            "AsyncioLockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, capture_pct=1.0, tracer=None)",
        ),
        (
            AsyncioSemaphoreCollector,
            "AsyncioSemaphoreCollector(status=<ServiceStatus.STOPPED: 'stopped'>, capture_pct=1.0, tracer=None)",
        ),
        (
            AsyncioBoundedSemaphoreCollector,
            "AsyncioBoundedSemaphoreCollector(status=<ServiceStatus.STOPPED: 'stopped'>, capture_pct=1.0, tracer=None)",
        ),
        (
            AsyncioConditionCollector,
            "AsyncioConditionCollector(status=<ServiceStatus.STOPPED: 'stopped'>, capture_pct=1.0, tracer=None)",
        ),
    ],
)
def test_collector_repr(collector_class: CollectorTypeClass, expected_repr: str) -> None:
    test_collector._test_repr(collector_class, expected_repr)


@pytest.mark.asyncio
class BaseAsyncioLockCollectorTest:
    """Base test class for asyncio lock collectors.

    Child classes must implement:
        - collector_class: The collector class to test
        - lock_class: The asyncio lock class to test
    """

    @property
    def collector_class(self) -> CollectorTypeClass:
        raise NotImplementedError("Child classes must implement collector_class")

    @property
    def lock_class(self) -> LockTypeClass:
        raise NotImplementedError("Child classes must implement lock_class")

    def setup_method(self, method: Callable[..., Any]) -> None:
        self.test_name: str = method.__qualname__ if PY_311_OR_ABOVE else method.__name__
        self.output_prefix: str = "/tmp" + os.sep + self.test_name
        self.output_filename: str = self.output_prefix + "." + str(os.getpid())

        assert ddup.is_available, "ddup is not available"
        ddup.config(
            env="test",
            service="test_asyncio",
            version="my_version",
            output_filename=self.output_prefix,
        )
        ddup.start()

    def teardown_method(self) -> None:
        for f in glob.glob(self.output_prefix + "*"):
            try:
                os.remove(f)
            except Exception as e:
                print("Error while deleting file: ", e)

    async def test_lock_events(self) -> None:
        """Test basic acquire/release event profiling."""
        with self.collector_class(capture_pct=100):
            lock = self.lock_class()  # !CREATE! asyncio_test_lock_events
            await lock.acquire()  # !ACQUIRE! asyncio_test_lock_events
            assert lock.locked()
            lock.release()  # !RELEASE! asyncio_test_lock_events

        ddup.upload()

        linenos = get_lock_linenos("asyncio_test_lock_events")
        profile = pprof_utils.parse_newest_profile(self.output_filename)
        expected_thread_id = _thread.get_ident()
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="lock",
                    thread_id=expected_thread_id,
                )
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="lock",
                    thread_id=expected_thread_id,
                )
            ],
        )

    async def test_lock_events_tracer(self, tracer: Any) -> None:
        """Test event profiling with tracer integration."""
        tracer._endpoint_call_counter_span_processor.enable()
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB

        with self.collector_class(capture_pct=100, tracer=tracer):
            lock = self.lock_class()  # !CREATE! asyncio_test_lock_events_tracer_1
            await lock.acquire()  # !ACQUIRE! asyncio_test_lock_events_tracer_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = self.lock_class()  # !CREATE! asyncio_test_lock_events_tracer_2
                await lock2.acquire()  # !ACQUIRE! asyncio_test_lock_events_tracer_2
                lock.release()  # !RELEASE! asyncio_test_lock_events_tracer_1
                span_id = t.span_id
            lock2.release()  # !RELEASE! asyncio_test_lock_events_tracer_2

            lock_ctx = self.lock_class()  # !CREATE! asyncio_test_lock_events_tracer_3
            async with lock_ctx:  # !ACQUIRE! !RELEASE! asyncio_test_lock_events_tracer_3
                pass
        ddup.upload(tracer=tracer)

        linenos_1 = get_lock_linenos("asyncio_test_lock_events_tracer_1")
        linenos_2 = get_lock_linenos("asyncio_test_lock_events_tracer_2")
        linenos_3 = get_lock_linenos("asyncio_test_lock_events_tracer_3", with_stmt=True)

        profile = pprof_utils.parse_newest_profile(self.output_filename)
        expected_thread_id = _thread.get_ident()

        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos_1,
                    lock_name="lock",
                    thread_id=expected_thread_id,
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos_2,
                    lock_name="lock2",
                    span_id=span_id,
                    trace_endpoint=resource,
                    trace_type=span_type,
                    thread_id=expected_thread_id,
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos_3,
                    lock_name="lock_ctx",
                    thread_id=expected_thread_id,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos_1,
                    lock_name="lock",
                    span_id=span_id,
                    trace_endpoint=resource,
                    trace_type=span_type,
                    thread_id=expected_thread_id,
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos_2,
                    lock_name="lock2",
                    thread_id=expected_thread_id,
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name=self.test_name,
                    filename=os.path.basename(__file__),
                    linenos=linenos_3,
                    lock_name="lock_ctx",
                    thread_id=expected_thread_id,
                ),
            ],
        )

    async def test_subclassing_wrapped_lock(self) -> None:
        """Test that subclassing of a wrapped lock type when profiling is active."""
        with self.collector_class(capture_pct=100):
            assert isinstance(self.lock_class, LockAllocatorWrapper)

            # This should NOT raise TypeError
            class CustomLock(self.lock_class):  # type: ignore[misc]
                def __init__(self) -> None:
                    super().__init__()

            # Verify subclassing and functionality
            custom_lock: CustomLock = CustomLock()
            await custom_lock.acquire()
            assert custom_lock.locked()
            custom_lock.release()
            assert not custom_lock.locked()


class TestAsyncioLockCollector(BaseAsyncioLockCollectorTest):
    """Test asyncio.Lock profiling."""

    @property
    def collector_class(self) -> type[AsyncioLockCollector]:
        return AsyncioLockCollector

    @property
    def lock_class(self) -> type[asyncio.Lock]:
        return asyncio.Lock


class TestAsyncioSemaphoreCollector(BaseAsyncioLockCollectorTest):
    """Test asyncio.Semaphore profiling."""

    @property
    def collector_class(self) -> type[AsyncioSemaphoreCollector]:
        return AsyncioSemaphoreCollector

    @property
    def lock_class(self) -> type[asyncio.Semaphore]:
        return asyncio.Semaphore


class TestAsyncioBoundedSemaphoreCollector(BaseAsyncioLockCollectorTest):
    """Test asyncio.BoundedSemaphore profiling."""

    @property
    def collector_class(self) -> type[AsyncioBoundedSemaphoreCollector]:
        return AsyncioBoundedSemaphoreCollector

    @property
    def lock_class(self) -> type[asyncio.BoundedSemaphore]:
        return asyncio.BoundedSemaphore

    async def test_bounded_behavior_preserved(self) -> None:
        """Test that profiling wrapper preserves BoundedSemaphore's bounded behavior.

        This verifies the wrapper doesn't interfere with BoundedSemaphore's unique characteristic:
        raising ValueError when releasing beyond the initial value.
        """
        with self.collector_class(capture_pct=100):
            bs = asyncio.BoundedSemaphore(1)
            await bs.acquire()
            bs.release()
            # BoundedSemaphore should raise ValueError when releasing more than initial value
            with pytest.raises(ValueError, match="BoundedSemaphore released too many times"):
                bs.release()


class TestAsyncioConditionCollector(BaseAsyncioLockCollectorTest):
    """Test asyncio.Condition profiling."""

    @property
    def collector_class(self) -> type[AsyncioConditionCollector]:
        return AsyncioConditionCollector

    @property
    def lock_class(self) -> type[asyncio.Condition]:
        return asyncio.Condition
