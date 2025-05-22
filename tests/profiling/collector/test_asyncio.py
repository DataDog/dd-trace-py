import _thread
import asyncio
import glob
import os
import uuid

import pytest

from ddtrace import ext
from ddtrace.internal.datadog.profiling import ddup
from ddtrace.profiling.collector import asyncio as collector_asyncio
from tests.profiling.collector import pprof_utils
from tests.profiling.collector import test_collector
from tests.profiling.collector.lock_utils import get_lock_linenos
from tests.profiling.collector.lock_utils import init_linenos


init_linenos(__file__)


def test_repr():
    test_collector._test_repr(
        collector_asyncio.AsyncioLockCollector,
        "AsyncioLockCollector(status=<ServiceStatus.STOPPED: 'stopped'>, "
        "recorder=Recorder(default_max_events=16384, max_events={}), capture_pct=1.0, nframes=64, "
        "endpoint_collection_enabled=True, export_libdd_enabled=True, tracer=None)",
    )


@pytest.mark.asyncio
class TestAsyncioLockCollector:
    def setup_method(self, method):
        self.test_name = method.__name__
        self.output_prefix = "/tmp" + os.sep + self.test_name
        self.output_filename = self.output_prefix + "." + str(os.getpid())

        assert ddup.is_available, "ddup is not available"
        ddup.config(
            env="test",
            service="test_asyncio",
            version="my_version",
        )
        ddup.start()

    def teardown_method(self):
        for f in glob.glob(self.output_prefix + "*"):
            try:
                os.remove(f)
            except Exception as e:
                print("Error while deleting file: ", e)

    async def test_asyncio_lock_events(self):
        with collector_asyncio.AsyncioLockCollector(None, capture_pct=100, export_libdd_enabled=True):
            lock = asyncio.Lock()  # !CREATE! test_asyncio_lock_events
            await lock.acquire()  # !ACQUIRE! test_asyncio_lock_events
            assert lock.locked()
            lock.release()  # !RELEASE! test_asyncio_lock_events

        ddup.upload(output_filename=self.output_filename)

        linenos = get_lock_linenos("test_asyncio_lock_events")
        profile = pprof_utils.parse_profile(self.output_filename)
        expected_thread_id = _thread.get_ident()
        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="test_asyncio_lock_events",
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="lock",
                    thread_id=expected_thread_id,
                )
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="test_asyncio_lock_events",
                    filename=os.path.basename(__file__),
                    linenos=linenos,
                    lock_name="lock",
                    thread_id=expected_thread_id,
                )
            ],
        )

    async def test_asyncio_lock_events_tracer(self, tracer):
        tracer._endpoint_call_counter_span_processor.enable()
        resource = str(uuid.uuid4())
        span_type = ext.SpanTypes.WEB

        with collector_asyncio.AsyncioLockCollector(None, capture_pct=100, export_libdd_enabled=True, tracer=tracer):
            lock = asyncio.Lock()  # !CREATE! test_asyncio_lock_events_tracer_1
            await lock.acquire()  # !ACQUIRE! test_asyncio_lock_events_tracer_1
            with tracer.trace("test", resource=resource, span_type=span_type) as t:
                lock2 = asyncio.Lock()  # !CREATE! test_asyncio_lock_events_tracer_2
                await lock2.acquire()  # !ACQUIRE! test_asyncio_lock_events_tracer_2
                lock.release()  # !RELEASE! test_asyncio_lock_events_tracer_1
                span_id = t.span_id
            lock2.release()  # !RELEASE! test_asyncio_lock_events_tracer_2

            lock_ctx = asyncio.Lock()  # !CREATE! test_asyncio_lock_events_tracer_3
            async with lock_ctx:  # !ACQUIRE! !RELEASE! test_asyncio_lock_events_tracer_3
                pass
        ddup.upload(tracer=tracer, output_filename=self.output_filename)

        linenos_1 = get_lock_linenos("test_asyncio_lock_events_tracer_1")
        linenos_2 = get_lock_linenos("test_asyncio_lock_events_tracer_2")
        linenos_3 = get_lock_linenos("test_asyncio_lock_events_tracer_3", with_stmt=True)

        profile = pprof_utils.parse_profile(self.output_filename)
        expected_thread_id = _thread.get_ident()

        pprof_utils.assert_lock_events(
            profile,
            expected_acquire_events=[
                pprof_utils.LockAcquireEvent(
                    caller_name="test_asyncio_lock_events_tracer",
                    filename=os.path.basename(__file__),
                    linenos=linenos_1,
                    lock_name="lock",
                    thread_id=expected_thread_id,
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name="test_asyncio_lock_events_tracer",
                    filename=os.path.basename(__file__),
                    linenos=linenos_2,
                    lock_name="lock2",
                    span_id=span_id,
                    trace_endpoint=resource,
                    trace_type=span_type,
                    thread_id=expected_thread_id,
                ),
                pprof_utils.LockAcquireEvent(
                    caller_name="test_asyncio_lock_events_tracer",
                    filename=os.path.basename(__file__),
                    linenos=linenos_3,
                    lock_name="lock_ctx",
                    thread_id=expected_thread_id,
                ),
            ],
            expected_release_events=[
                pprof_utils.LockReleaseEvent(
                    caller_name="test_asyncio_lock_events_tracer",
                    filename=os.path.basename(__file__),
                    linenos=linenos_1,
                    lock_name="lock",
                    span_id=span_id,
                    trace_endpoint=resource,
                    trace_type=span_type,
                    thread_id=expected_thread_id,
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name="test_asyncio_lock_events_tracer",
                    filename=os.path.basename(__file__),
                    linenos=linenos_2,
                    lock_name="lock2",
                    thread_id=expected_thread_id,
                ),
                pprof_utils.LockReleaseEvent(
                    caller_name="test_asyncio_lock_events_tracer",
                    filename=os.path.basename(__file__),
                    linenos=linenos_3,
                    lock_name="lock_ctx",
                    thread_id=expected_thread_id,
                ),
            ],
        )
