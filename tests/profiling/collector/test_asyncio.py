from __future__ import annotations

import asyncio
import sys
from typing import Union

import pytest

from ddtrace.profiling.collector._lock import _LockAllocatorWrapper as LockAllocatorWrapper
from ddtrace.profiling.collector.asyncio import AsyncioBoundedSemaphoreCollector
from ddtrace.profiling.collector.asyncio import AsyncioConditionCollector
from ddtrace.profiling.collector.asyncio import AsyncioLockCollector
from ddtrace.profiling.collector.asyncio import AsyncioSemaphoreCollector
from tests.profiling.collector import test_collector
from tests.profiling.collector.lock_test_common import assert_pep604_type_union_syntax
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


# ---------------------------------------------------------------------------
# BaseAsyncioLockCollectorTest — non-ddup tests kept as a class
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
class BaseAsyncioLockCollectorTest:
    """Base test class for asyncio lock collectors — non-ddup-upload tests only.

    Tests that call ddup.upload() are converted to @pytest.mark.subprocess functions below.
    Child classes must implement collector_class and lock_class.
    """

    @property
    def collector_class(self) -> CollectorTypeClass:
        raise NotImplementedError("Child classes must implement collector_class")

    @property
    def lock_class(self) -> LockTypeClass:
        raise NotImplementedError("Child classes must implement lock_class")

    async def test_subclassing_wrapped_lock(self) -> None:
        """Test that subclassing of a wrapped lock type when profiling is active."""
        with self.collector_class(capture_pct=100):
            assert isinstance(self.lock_class, LockAllocatorWrapper)

            # This should NOT raise TypeError
            class CustomLock(self.lock_class):  # type: ignore[unreachable, name-defined]
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
        """Test that profiling wrapper preserves BoundedSemaphore's bounded behavior."""
        with self.collector_class(capture_pct=100):
            bs = asyncio.BoundedSemaphore(1)
            await bs.acquire()
            bs.release()
            # BoundedSemaphore should raise ValueError when releasing beyond the initial value
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


class TestAsyncGenericLockProfiling:
    """Generic asyncio lock profiling tests that run once with asyncio.Lock.

    Mirrors TestGenericLockProfiling in test_threading.py. Tests here verify
    _LockAllocatorWrapper behavior that is shared across all asyncio lock types.
    """

    @property
    def collector_class(self) -> type[AsyncioLockCollector]:
        return AsyncioLockCollector

    @property
    def lock_class(self) -> type[asyncio.Lock]:
        return asyncio.Lock

    @pytest.mark.skipif(sys.version_info < (3, 10), reason="PEP 604 type union syntax requires Python 3.10+")
    def test_pep604_type_union_syntax(self) -> None:
        """Test that PEP 604 type union syntax works with wrapped lock classes.

        Reproduces https://github.com/DataDog/dd-trace-py/issues/16375
        """
        with self.collector_class(capture_pct=100):
            assert_pep604_type_union_syntax(self.lock_class)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Subprocess versions of BaseAsyncioLockCollectorTest ddup-upload tests
# Each function covers ONE (collector_class, lock_class, test_method) combination.
# ---------------------------------------------------------------------------


@pytest.mark.subprocess(err=None, env=dict(DD_PROFILING_FILE_PATH=__file__))
def test_asyncio_lock_lock_events() -> None:
    import _thread
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.asyncio import AsyncioLockCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos
    from tests.profiling.utils import with_profiling_test_agent

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    async def test_lock_events():
        lock = asyncio.Lock()  # !CREATE! asyncio_test_lock_events
        await lock.acquire()  # !ACQUIRE! asyncio_test_lock_events
        assert lock.locked()
        lock.release()  # !RELEASE! asyncio_test_lock_events

    assert ddup.is_available, "ddup is not available"
    with with_profiling_test_agent() as agent:
        ddup.config(env="test", service="test_asyncio_lock_lock_events", version="my_version")
        ddup.start()

        with AsyncioLockCollector(capture_pct=100):
            asyncio.run(test_lock_events())

        ddup.upload()
        profile = pprof_utils.get_profile_from_agent(agent)

    linenos = get_lock_linenos("asyncio_test_lock_events")
    expected_thread_id = _thread.get_ident()
    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events",
                filename="test_asyncio.py",
                linenos=linenos,
                lock_name="lock",
                thread_id=expected_thread_id,
            )
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events",
                filename="test_asyncio.py",
                linenos=linenos,
                lock_name="lock",
                thread_id=expected_thread_id,
            )
        ],
    )


@pytest.mark.subprocess(err=None, env=dict(DD_PROFILING_FILE_PATH=__file__))
def test_asyncio_semaphore_lock_events() -> None:
    import _thread
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.asyncio import AsyncioSemaphoreCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos
    from tests.profiling.utils import with_profiling_test_agent

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    async def test_lock_events():
        lock = asyncio.Semaphore()  # !CREATE! asyncio_semaphore_lock_events
        await lock.acquire()  # !ACQUIRE! asyncio_semaphore_lock_events
        assert lock.locked()
        lock.release()  # !RELEASE! asyncio_semaphore_lock_events

    assert ddup.is_available, "ddup is not available"
    with with_profiling_test_agent() as agent:
        ddup.config(env="test", service="test_asyncio_semaphore_lock_events", version="my_version")
        ddup.start()

        with AsyncioSemaphoreCollector(capture_pct=100):
            asyncio.run(test_lock_events())

        ddup.upload()
        profile = pprof_utils.get_profile_from_agent(agent)

    linenos = get_lock_linenos("asyncio_semaphore_lock_events")
    expected_thread_id = _thread.get_ident()
    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events",
                filename="test_asyncio.py",
                linenos=linenos,
                lock_name="lock",
                thread_id=expected_thread_id,
            )
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events",
                filename="test_asyncio.py",
                linenos=linenos,
                lock_name="lock",
                thread_id=expected_thread_id,
            )
        ],
    )


@pytest.mark.subprocess(err=None, env=dict(DD_PROFILING_FILE_PATH=__file__))
def test_asyncio_bounded_semaphore_lock_events() -> None:
    import _thread
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.asyncio import AsyncioBoundedSemaphoreCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos
    from tests.profiling.utils import with_profiling_test_agent

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    async def test_lock_events():
        lock = asyncio.BoundedSemaphore()  # !CREATE! asyncio_bounded_semaphore_lock_events
        await lock.acquire()  # !ACQUIRE! asyncio_bounded_semaphore_lock_events
        assert lock.locked()
        lock.release()  # !RELEASE! asyncio_bounded_semaphore_lock_events

    assert ddup.is_available, "ddup is not available"
    with with_profiling_test_agent() as agent:
        ddup.config(env="test", service="test_asyncio_bounded_semaphore_lock_events", version="my_version")
        ddup.start()

        with AsyncioBoundedSemaphoreCollector(capture_pct=100):
            asyncio.run(test_lock_events())

        ddup.upload()
        profile = pprof_utils.get_profile_from_agent(agent)

    linenos = get_lock_linenos("asyncio_bounded_semaphore_lock_events")
    expected_thread_id = _thread.get_ident()
    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events",
                filename="test_asyncio.py",
                linenos=linenos,
                lock_name="lock",
                thread_id=expected_thread_id,
            )
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events",
                filename="test_asyncio.py",
                linenos=linenos,
                lock_name="lock",
                thread_id=expected_thread_id,
            )
        ],
    )


@pytest.mark.subprocess(err=None, env=dict(DD_PROFILING_FILE_PATH=__file__))
def test_asyncio_condition_lock_events() -> None:
    import _thread
    import asyncio
    import os

    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.asyncio import AsyncioConditionCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos
    from tests.profiling.utils import with_profiling_test_agent

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    async def test_lock_events():
        lock = asyncio.Condition()  # !CREATE! asyncio_condition_lock_events
        await lock.acquire()  # !ACQUIRE! asyncio_condition_lock_events
        assert lock.locked()
        lock.release()  # !RELEASE! asyncio_condition_lock_events

    assert ddup.is_available, "ddup is not available"
    with with_profiling_test_agent() as agent:
        ddup.config(env="test", service="test_asyncio_condition_lock_events", version="my_version")
        ddup.start()

        with AsyncioConditionCollector(capture_pct=100):
            asyncio.run(test_lock_events())

        ddup.upload()
        profile = pprof_utils.get_profile_from_agent(agent)

    linenos = get_lock_linenos("asyncio_condition_lock_events")
    expected_thread_id = _thread.get_ident()
    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events",
                filename="test_asyncio.py",
                linenos=linenos,
                lock_name="lock",
                thread_id=expected_thread_id,
            )
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events",
                filename="test_asyncio.py",
                linenos=linenos,
                lock_name="lock",
                thread_id=expected_thread_id,
            )
        ],
    )


@pytest.mark.subprocess(err=None, env=dict(DD_PROFILING_FILE_PATH=__file__))
def test_asyncio_lock_lock_events_tracer() -> None:
    import _thread
    import asyncio
    import os
    import uuid

    import ddtrace
    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.asyncio import AsyncioLockCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos
    from tests.profiling.utils import with_profiling_test_agent

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    tracer = ddtrace.trace.tracer
    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    async def test_lock_events_tracer():
        tracer._endpoint_call_counter_span_processor.enable()

        with AsyncioLockCollector(capture_pct=100, tracer=tracer):
            lock = asyncio.Lock()  # !CREATE! asyncio_lock_events_tracer_1
            await lock.acquire()  # !ACQUIRE! asyncio_lock_events_tracer_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = asyncio.Lock()  # !CREATE! asyncio_lock_events_tracer_2
                await lock2.acquire()  # !ACQUIRE! asyncio_lock_events_tracer_2
                lock.release()  # !RELEASE! asyncio_lock_events_tracer_1
                span_id = t.span_id
            lock2.release()  # !RELEASE! asyncio_lock_events_tracer_2

            lock_ctx = asyncio.Lock()  # !CREATE! asyncio_lock_events_tracer_3
            async with lock_ctx:  # !ACQUIRE! !RELEASE! asyncio_lock_events_tracer_3
                pass
        return span_id

    assert ddup.is_available, "ddup is not available"
    with with_profiling_test_agent() as agent:
        ddup.config(env="test", service="test_asyncio_lock_lock_events_tracer", version="my_version")
        ddup.start()
        span_id = asyncio.run(test_lock_events_tracer())
        ddup.upload(tracer=tracer)
        profile = pprof_utils.get_profile_from_agent(agent)

    linenos_1 = get_lock_linenos("asyncio_lock_events_tracer_1")
    linenos_2 = get_lock_linenos("asyncio_lock_events_tracer_2")
    linenos_3 = get_lock_linenos("asyncio_lock_events_tracer_3", with_stmt=True)
    expected_thread_id = _thread.get_ident()

    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_1,
                lock_name="lock",
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_2,
                lock_name="lock2",
                span_id=span_id,
                trace_endpoint=resource,
                trace_type=span_type,
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_3,
                lock_name="lock_ctx",
                thread_id=expected_thread_id,
            ),
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_1,
                lock_name="lock",
                span_id=span_id,
                trace_endpoint=resource,
                trace_type=span_type,
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_2,
                lock_name="lock2",
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_3,
                lock_name="lock_ctx",
                thread_id=expected_thread_id,
            ),
        ],
    )


@pytest.mark.subprocess(err=None, env=dict(DD_PROFILING_FILE_PATH=__file__))
def test_asyncio_semaphore_lock_events_tracer() -> None:
    import _thread
    import asyncio
    import os
    import uuid

    import ddtrace
    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.asyncio import AsyncioSemaphoreCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos
    from tests.profiling.utils import with_profiling_test_agent

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    tracer = ddtrace.trace.tracer
    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    async def test_lock_events_tracer():
        tracer._endpoint_call_counter_span_processor.enable()

        with AsyncioSemaphoreCollector(capture_pct=100, tracer=tracer):
            lock = asyncio.Semaphore()  # !CREATE! asyncio_semaphore_events_tracer_1
            await lock.acquire()  # !ACQUIRE! asyncio_semaphore_events_tracer_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = asyncio.Semaphore()  # !CREATE! asyncio_semaphore_events_tracer_2
                await lock2.acquire()  # !ACQUIRE! asyncio_semaphore_events_tracer_2
                lock.release()  # !RELEASE! asyncio_semaphore_events_tracer_1
                span_id = t.span_id
            lock2.release()  # !RELEASE! asyncio_semaphore_events_tracer_2

            lock_ctx = asyncio.Semaphore()  # !CREATE! asyncio_semaphore_events_tracer_3
            async with lock_ctx:  # !ACQUIRE! !RELEASE! asyncio_semaphore_events_tracer_3
                pass
        return span_id

    assert ddup.is_available, "ddup is not available"
    with with_profiling_test_agent() as agent:
        ddup.config(env="test", service="test_asyncio_semaphore_lock_events_tracer", version="my_version")
        ddup.start()
        span_id = asyncio.run(test_lock_events_tracer())
        ddup.upload(tracer=tracer)
        profile = pprof_utils.get_profile_from_agent(agent)

    linenos_1 = get_lock_linenos("asyncio_semaphore_events_tracer_1")
    linenos_2 = get_lock_linenos("asyncio_semaphore_events_tracer_2")
    linenos_3 = get_lock_linenos("asyncio_semaphore_events_tracer_3", with_stmt=True)
    expected_thread_id = _thread.get_ident()

    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_1,
                lock_name="lock",
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_2,
                lock_name="lock2",
                span_id=span_id,
                trace_endpoint=resource,
                trace_type=span_type,
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_3,
                lock_name="lock_ctx",
                thread_id=expected_thread_id,
            ),
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_1,
                lock_name="lock",
                span_id=span_id,
                trace_endpoint=resource,
                trace_type=span_type,
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_2,
                lock_name="lock2",
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_3,
                lock_name="lock_ctx",
                thread_id=expected_thread_id,
            ),
        ],
    )


@pytest.mark.subprocess(err=None, env=dict(DD_PROFILING_FILE_PATH=__file__))
def test_asyncio_bounded_semaphore_lock_events_tracer() -> None:
    import _thread
    import asyncio
    import os
    import uuid

    import ddtrace
    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.asyncio import AsyncioBoundedSemaphoreCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos
    from tests.profiling.utils import with_profiling_test_agent

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    tracer = ddtrace.trace.tracer
    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    async def test_lock_events_tracer():
        tracer._endpoint_call_counter_span_processor.enable()

        with AsyncioBoundedSemaphoreCollector(capture_pct=100, tracer=tracer):
            lock = asyncio.BoundedSemaphore()  # !CREATE! asyncio_bsem_events_tracer_1
            await lock.acquire()  # !ACQUIRE! asyncio_bsem_events_tracer_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = asyncio.BoundedSemaphore()  # !CREATE! asyncio_bsem_events_tracer_2
                await lock2.acquire()  # !ACQUIRE! asyncio_bsem_events_tracer_2
                lock.release()  # !RELEASE! asyncio_bsem_events_tracer_1
                span_id = t.span_id
            lock2.release()  # !RELEASE! asyncio_bsem_events_tracer_2

            lock_ctx = asyncio.BoundedSemaphore()  # !CREATE! asyncio_bsem_events_tracer_3
            async with lock_ctx:  # !ACQUIRE! !RELEASE! asyncio_bsem_events_tracer_3
                pass
        return span_id

    assert ddup.is_available, "ddup is not available"
    with with_profiling_test_agent() as agent:
        ddup.config(env="test", service="test_asyncio_bounded_semaphore_lock_events_tracer", version="my_version")
        ddup.start()
        span_id = asyncio.run(test_lock_events_tracer())
        ddup.upload(tracer=tracer)
        profile = pprof_utils.get_profile_from_agent(agent)

    linenos_1 = get_lock_linenos("asyncio_bsem_events_tracer_1")
    linenos_2 = get_lock_linenos("asyncio_bsem_events_tracer_2")
    linenos_3 = get_lock_linenos("asyncio_bsem_events_tracer_3", with_stmt=True)
    expected_thread_id = _thread.get_ident()

    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_1,
                lock_name="lock",
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_2,
                lock_name="lock2",
                span_id=span_id,
                trace_endpoint=resource,
                trace_type=span_type,
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_3,
                lock_name="lock_ctx",
                thread_id=expected_thread_id,
            ),
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_1,
                lock_name="lock",
                span_id=span_id,
                trace_endpoint=resource,
                trace_type=span_type,
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_2,
                lock_name="lock2",
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_3,
                lock_name="lock_ctx",
                thread_id=expected_thread_id,
            ),
        ],
    )


@pytest.mark.subprocess(err=None, env=dict(DD_PROFILING_FILE_PATH=__file__))
def test_asyncio_condition_lock_events_tracer() -> None:
    import _thread
    import asyncio
    import os
    import uuid

    import ddtrace
    from ddtrace import ext
    from ddtrace.internal.datadog.profiling import ddup
    from ddtrace.profiling.collector.asyncio import AsyncioConditionCollector
    from tests.profiling.collector import pprof_utils
    from tests.profiling.collector.lock_utils import get_lock_linenos
    from tests.profiling.collector.lock_utils import init_linenos
    from tests.profiling.utils import with_profiling_test_agent

    init_linenos(os.environ["DD_PROFILING_FILE_PATH"])

    tracer = ddtrace.trace.tracer
    resource = str(uuid.uuid4())
    span_type = ext.SpanTypes.WEB

    async def test_lock_events_tracer():
        tracer._endpoint_call_counter_span_processor.enable()

        with AsyncioConditionCollector(capture_pct=100, tracer=tracer):
            lock = asyncio.Condition()  # !CREATE! asyncio_condition_events_tracer_1
            await lock.acquire()  # !ACQUIRE! asyncio_condition_events_tracer_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = asyncio.Condition()  # !CREATE! asyncio_condition_events_tracer_2
                await lock2.acquire()  # !ACQUIRE! asyncio_condition_events_tracer_2
                lock.release()  # !RELEASE! asyncio_condition_events_tracer_1
                span_id = t.span_id
            lock2.release()  # !RELEASE! asyncio_condition_events_tracer_2

            lock_ctx = asyncio.Condition()  # !CREATE! asyncio_condition_events_tracer_3
            async with lock_ctx:  # !ACQUIRE! !RELEASE! asyncio_condition_events_tracer_3
                pass
        return span_id

    assert ddup.is_available, "ddup is not available"
    with with_profiling_test_agent() as agent:
        ddup.config(env="test", service="test_asyncio_condition_lock_events_tracer", version="my_version")
        ddup.start()
        span_id = asyncio.run(test_lock_events_tracer())
        ddup.upload(tracer=tracer)
        profile = pprof_utils.get_profile_from_agent(agent)

    linenos_1 = get_lock_linenos("asyncio_condition_events_tracer_1")
    linenos_2 = get_lock_linenos("asyncio_condition_events_tracer_2")
    linenos_3 = get_lock_linenos("asyncio_condition_events_tracer_3", with_stmt=True)
    expected_thread_id = _thread.get_ident()

    pprof_utils.assert_lock_events(
        profile,
        expected_acquire_events=[
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_1,
                lock_name="lock",
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_2,
                lock_name="lock2",
                span_id=span_id,
                trace_endpoint=resource,
                trace_type=span_type,
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockAcquireEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_3,
                lock_name="lock_ctx",
                thread_id=expected_thread_id,
            ),
        ],
        expected_release_events=[
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_1,
                lock_name="lock",
                span_id=span_id,
                trace_endpoint=resource,
                trace_type=span_type,
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_2,
                lock_name="lock2",
                thread_id=expected_thread_id,
            ),
            pprof_utils.LockReleaseEvent(
                caller_name="test_lock_events_tracer",
                filename="test_asyncio.py",
                linenos=linenos_3,
                lock_name="lock_ctx",
                thread_id=expected_thread_id,
            ),
        ],
    )
