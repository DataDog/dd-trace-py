import concurrent.futures
import ctypes
import os
import sys
import threading

import pytest

from ddtrace._trace.provider import DefaultContextProvider
from ddtrace._trace.tracer import Tracer


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
