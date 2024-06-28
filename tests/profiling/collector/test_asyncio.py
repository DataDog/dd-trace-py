import asyncio
import uuid

import pytest
from six.moves import _thread

from ddtrace.profiling import recorder
from ddtrace.profiling.collector import asyncio as collector_asyncio


@pytest.mark.asyncio
async def test_lock_acquire_events():
    r = recorder.Recorder()
    with collector_asyncio.AsyncioLockCollector(r, capture_pct=100):
        lock = asyncio.Lock()
        await lock.acquire()
        assert lock.locked()
    assert len(r.events[collector_asyncio.AsyncioLockAcquireEvent]) == 1
    assert len(r.events[collector_asyncio.AsyncioLockReleaseEvent]) == 0
    event = r.events[collector_asyncio.AsyncioLockAcquireEvent][0]
    assert event.lock_name == "test_asyncio.py:16:lock"
    assert event.thread_id == _thread.get_ident()
    assert event.wait_time_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__, 16, "test_lock_acquire_events", "")
    assert event.sampling_pct == 100


@pytest.mark.asyncio
async def test_asyncio_lock_release_events():
    r = recorder.Recorder()
    with collector_asyncio.AsyncioLockCollector(r, capture_pct=100):
        lock = asyncio.Lock()
        assert await lock.acquire()
        assert lock.locked()
        lock.release()
    assert len(r.events[collector_asyncio.AsyncioLockAcquireEvent]) == 1
    assert len(r.events[collector_asyncio.AsyncioLockReleaseEvent]) == 1
    event = r.events[collector_asyncio.AsyncioLockReleaseEvent][0]
    assert event.lock_name == "test_asyncio.py:38:lock"
    assert event.thread_id == _thread.get_ident()
    assert event.locked_for_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__, 38, "test_asyncio_lock_release_events", "")
    assert event.sampling_pct == 100


@pytest.mark.asyncio
async def test_lock_events_tracer(tracer):
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    r = recorder.Recorder()
    with collector_asyncio.AsyncioLockCollector(r, tracer=tracer, capture_pct=100):
        lock = asyncio.Lock()
        await lock.acquire()
        with tracer.trace("test", resource=resource, span_type=span_type) as t:
            lock2 = asyncio.Lock()
            await lock2.acquire()
            lock.release()
            span_id = t.span_id
        lock2.release()

        lock_ctx = asyncio.Lock()
        async with lock_ctx:
            pass
    events = r.reset()
    # The tracer might use locks, so we need to look into every event to assert we got ours
    lock1_acquire, lock1_release, lock2_acquire, lock2_release = (
        "test_asyncio.py:59:lock",
        "test_asyncio.py:63:lock",
        "test_asyncio.py:62:lock2",
        "test_asyncio.py:65:lock2",
    )
    for event_type in (collector_asyncio.AsyncioLockAcquireEvent, collector_asyncio.AsyncioLockReleaseEvent):
        if event_type == collector_asyncio.AsyncioLockAcquireEvent:
            assert {lock1_acquire, lock2_acquire}.issubset({e.lock_name for e in events[event_type]})
        elif event_type == collector_asyncio.AsyncioLockReleaseEvent:
            assert {lock1_release, lock2_release}.issubset({e.lock_name for e in events[event_type]})
        for event in events[event_type]:
            if event.lock_name in [lock1_acquire, lock2_release]:
                assert event.span_id is None
                assert event.trace_resource_container is None
                assert event.trace_type is None
            elif event.lock_name in [lock2_acquire, lock1_release]:
                assert event.span_id == span_id
                assert event.trace_resource_container[0] == t.resource
                assert event.trace_type == t.span_type
