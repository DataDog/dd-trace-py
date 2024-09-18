import _thread
import asyncio
import uuid

import pytest

from ddtrace.profiling import recorder
from ddtrace.profiling.collector import asyncio as collector_asyncio
from tests.profiling.collector.lock_utils import get_lock_linenos
from tests.profiling.collector.lock_utils import init_linenos


init_linenos(__file__)


@pytest.mark.asyncio
async def test_lock_acquire_events():
    r = recorder.Recorder()
    with collector_asyncio.AsyncioLockCollector(r, capture_pct=100):
        lock = asyncio.Lock()  # !CREATE! test_lock_acquire_events_asyncio
        await lock.acquire()  # !ACQUIRE! test_lock_acquire_events_asyncio
        assert lock.locked()
    assert len(r.events[collector_asyncio.AsyncioLockAcquireEvent]) == 1
    assert len(r.events[collector_asyncio.AsyncioLockReleaseEvent]) == 0
    event = r.events[collector_asyncio.AsyncioLockAcquireEvent][0]
    linenos = get_lock_linenos("test_lock_acquire_events_asyncio")
    assert event.lock_name == "test_asyncio.py:{}:lock".format(linenos.create)
    assert event.thread_id == _thread.get_ident()
    assert event.wait_time_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__, linenos.acquire, "test_lock_acquire_events", "")
    assert event.sampling_pct == 100


@pytest.mark.asyncio
async def test_asyncio_lock_release_events():
    r = recorder.Recorder()
    with collector_asyncio.AsyncioLockCollector(r, capture_pct=100):
        lock = asyncio.Lock()  # !CREATE! test_asyncio_lock_release_events
        assert await lock.acquire()  # !ACQUIRE! test_asyncio_lock_release_events
        assert lock.locked()
        lock.release()  # !RELEASE! test_asyncio_lock_release_events
    assert len(r.events[collector_asyncio.AsyncioLockAcquireEvent]) == 1
    assert len(r.events[collector_asyncio.AsyncioLockReleaseEvent]) == 1
    event = r.events[collector_asyncio.AsyncioLockReleaseEvent][0]
    linenos = get_lock_linenos("test_asyncio_lock_release_events")
    assert event.lock_name == "test_asyncio.py:{}:lock".format(linenos.create)
    assert event.thread_id == _thread.get_ident()
    assert event.locked_for_ns >= 0
    # It's called through pytest so I'm sure it's gonna be that long, right?
    assert len(event.frames) > 3
    assert event.nframes > 3
    assert event.frames[0] == (__file__, linenos.release, "test_asyncio_lock_release_events", "")
    assert event.sampling_pct == 100


@pytest.mark.asyncio
async def test_lock_events_tracer(tracer):
    resource = str(uuid.uuid4())
    span_type = str(uuid.uuid4())
    r = recorder.Recorder()
    with collector_asyncio.AsyncioLockCollector(r, tracer=tracer, capture_pct=100):
        lock = asyncio.Lock()  # !CREATE! test_lock_events_tracer_1
        await lock.acquire()  # !ACQUIRE! test_lock_events_tracer_1
        with tracer.trace("test", resource=resource, span_type=span_type) as t:
            lock2 = asyncio.Lock()  # !CREATE! test_lock_events_tracer_2
            await lock2.acquire()  # !ACQUIRE! test_lock_events_tracer_2
            lock.release()  # !RELEASE! test_lock_events_tracer_1
            span_id = t.span_id
        lock2.release()  # !RELEASE! test_lock_events_tracer_2

        lock_ctx = asyncio.Lock()  # !CREATE! test_lock_events_tracer_3
        async with lock_ctx:  # !ACQUIRE! !RELEASE! test_lock_events_tracer_3
            pass
    events = r.reset()
    # The tracer might use locks, so we need to look into every event to assert we got ours
    linenos_1 = get_lock_linenos("test_lock_events_tracer_1")
    linenos_2 = get_lock_linenos("test_lock_events_tracer_2")
    linenos_3 = get_lock_linenos("test_lock_events_tracer_3", with_stmt=True)
    lock1_name = "test_asyncio.py:{}:lock".format(linenos_1.create)
    lock2_name = "test_asyncio.py:{}:lock2".format(linenos_2.create)
    lock3_name = "test_asyncio.py:{}:lock_ctx".format(linenos_3.create)
    lines_with_trace = [linenos_1.acquire, linenos_2.release, linenos_3.acquire, linenos_3.release]
    lines_without_trace = [linenos_2.acquire, linenos_1.release]
    for event_type in (collector_asyncio.AsyncioLockAcquireEvent, collector_asyncio.AsyncioLockReleaseEvent):
        assert {lock1_name, lock2_name, lock3_name}.issubset({e.lock_name for e in events[event_type]})
        for event in events[event_type]:
            if event.name in [lock1_name, lock2_name, lock3_name]:
                file_name, lineno, function_name, class_name = event.frames[0]
                assert file_name == __file__.replace(".pyc", ".py")
                assert lineno in lines_with_trace + lines_without_trace
                assert function_name == "test_lock_events_tracer"
                assert class_name == ""
                if lineno in lines_without_trace:
                    assert event.span_id is None
                    assert event.trace_resource_container is None
                    assert event.trace_type is None
                elif lineno in lines_with_trace:
                    assert event.span_id == span_id
                    assert event.trace_resource_container[0] == resource
                    assert event.trace_type == span_type
