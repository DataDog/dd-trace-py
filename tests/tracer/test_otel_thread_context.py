import asyncio
import concurrent.futures
import ctypes
import os
import sys
import threading

import pytest

from ddtrace._trace.provider import DefaultContextProvider
from ddtrace._trace.tracer import Tracer
from ddtrace.contrib.internal.asyncio.patch import patch as patch_asyncio
from ddtrace.contrib.internal.asyncio.patch import unpatch as unpatch_asyncio


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="OTel thread context is only published on Linux")


if sys.platform == "linux":
    from ddtrace.internal.native import _native
    from ddtrace.internal.native._native import detach_otel_thread_context

    class _ThreadContextRecord(ctypes.Structure):
        _fields_ = [
            ("trace_id", ctypes.c_ubyte * 16),
            ("span_id", ctypes.c_ubyte * 8),
            ("valid", ctypes.c_ubyte),
        ]

    _NATIVE_LIBRARY = ctypes.CDLL(_native.__file__)


def _published_span_id():
    slot = ctypes.c_void_p.in_dll(_NATIVE_LIBRARY, "otel_thread_ctx_v1")
    if slot.value is None:
        return None

    record = _ThreadContextRecord.from_address(slot.value)
    if not record.valid:
        return None
    return int.from_bytes(record.span_id, byteorder="big")


def test_span_context_is_published_and_detached(tracer: Tracer):
    with tracer.trace("test") as span:
        assert _published_span_id() == span.span_id

    assert _published_span_id() is None


def test_span_context_is_thread_local(tracer: Tracer):
    barrier = threading.Barrier(2)

    def trace(name):
        with tracer.trace(name) as span:
            barrier.wait()
            return span.span_id, _published_span_id()

    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        results = executor.map(trace, ("one", "two"))

    assert all(span_id == published_span_id for span_id, published_span_id in results)


def test_only_installed_context_provider_updates_thread_context(tracer: Tracer):
    uninstalled_provider = DefaultContextProvider()

    with tracer.trace("test") as span:
        uninstalled_provider.activate(None)

        assert _published_span_id() == span.span_id


def test_span_context_tracks_asyncio_task_switches(tracer: Tracer):
    was_patched = getattr(asyncio, "_datadog_patch", False)
    patch_asyncio()

    async def run_tasks() -> None:
        first_started = asyncio.Event()
        resume_first = asyncio.Event()
        first_resumed = asyncio.Event()

        async def first() -> None:
            with tracer.trace("first") as span:
                first_started.set()
                await resume_first.wait()
                assert _published_span_id() == span.span_id
                first_resumed.set()

        async def second() -> None:
            await first_started.wait()
            with tracer.trace("second"):
                resume_first.set()
                await first_resumed.wait()

        await asyncio.gather(first(), second())

    try:
        asyncio.run(run_tasks())
    finally:
        if not was_patched:
            unpatch_asyncio()


def test_span_context_skips_finished_span_captured_by_asyncio_handle(tracer: Tracer):
    was_patched = getattr(asyncio, "_datadog_patch", False)
    patch_asyncio()

    async def run_callback() -> None:
        loop = asyncio.get_running_loop()
        callback_finished = loop.create_future()

        with tracer.trace("parent") as parent:
            with tracer.trace("child"):

                def callback() -> None:
                    callback_finished.set_result(_published_span_id())

                loop.call_soon(callback)

            assert await callback_finished == parent.span_id

    try:
        asyncio.run(run_callback())
    finally:
        if not was_patched:
            unpatch_asyncio()


def test_span_context_is_reactivated_after_fork(tracer: Tracer):
    with tracer.trace("test") as span:
        if sys.platform == "linux":  # to satisfy the type checker outside of linux
            detach_otel_thread_context()
        pid = os.fork()
        if pid == 0:
            os._exit(0 if _published_span_id() == span.span_id else 1)

        tracer.context_provider.activate(span)
        _, status = os.waitpid(pid, 0)

    assert os.waitstatus_to_exitcode(status) == 0
