import asyncio
import concurrent.futures
from contextvars import Context
import ctypes
import os
import sys
import threading

import pytest

from ddtrace._trace.context import Context as DDContext
from ddtrace._trace.provider import DefaultContextProvider
from ddtrace._trace.tracer import Tracer
from ddtrace.contrib.internal.asyncio.patch import patch
from ddtrace.contrib.internal.asyncio.patch import unpatch
from ddtrace.internal import core
from ddtrace.internal.opentelemetry.thread_context import register_otel_thread_context_listener


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="OTel thread context is only published on Linux")


if sys.platform == "linux":
    from ddtrace.internal.native import _native
    from ddtrace.internal.native._native import detach_otel_thread_context

    class _ThreadContextRecord(ctypes.Structure):
        _fields_ = [
            ("trace_id", ctypes.c_ubyte * 16),
            ("span_id", ctypes.c_ubyte * 8),
            ("valid", ctypes.c_ubyte),
            ("trace_flags", ctypes.c_ubyte),
            ("attrs_data_size", ctypes.c_ushort),
            ("attrs_data", ctypes.c_ubyte * 612),
        ]

    _NATIVE_LIBRARY = ctypes.CDLL(_native.__file__)


def _published_context():
    slot = ctypes.c_void_p.in_dll(_NATIVE_LIBRARY, "otel_thread_ctx_v1")
    if slot.value is None:
        return None

    record = _ThreadContextRecord.from_address(slot.value)
    if not record.valid:
        return None

    attrs_data = bytes(record.attrs_data[: record.attrs_data_size])
    if len(attrs_data) < 18 or attrs_data[0] != 0 or attrs_data[1] != 16:
        return None
    return (
        int.from_bytes(record.trace_id, byteorder="big"),
        int.from_bytes(record.span_id, byteorder="big"),
        record.trace_flags,
        int(attrs_data[2:18], 16),
    )


def _published_span_id():
    context = _published_context()
    return None if context is None else context[1]


def _published_trace_flags():
    context = _published_context()
    return None if context is None else context[2]


@pytest.fixture(autouse=True)
def _register_otel_thread_context_listener(tracer):
    listeners = register_otel_thread_context_listener(tracer)
    assert listeners is not None
    activation_listener, context_switch_listener = listeners
    yield
    core.reset_listeners("ddtrace.context_provider.activate", activation_listener)
    core.reset_listeners("python.context.switch", context_switch_listener)


def test_span_context_is_published_and_detached(tracer: Tracer):
    with tracer.trace("test") as span:
        assert _published_span_id() == span.span_id

    assert _published_span_id() is None


def test_span_context_publishes_trace_flags(tracer: Tracer):
    with tracer.trace("test") as span:
        span.context.sampling_priority = 1
        tracer.context_provider.activate(span)
        assert _published_trace_flags() == 1

        span.context.sampling_priority = 0
        tracer.context_provider.activate(span)
        assert _published_trace_flags() == 0


def test_context_is_published_with_zero_local_root(tracer: Tracer):
    context = DDContext(trace_id=123, span_id=456, sampling_priority=1)

    tracer.context_provider.activate(context)

    assert _published_context() == (context.trace_id, context.span_id, 1, 0)

    context.sampling_priority = 0
    tracer.context_provider.activate(context)
    assert _published_context() == (context.trace_id, context.span_id, 0, 0)

    tracer.context_provider.activate(None)
    assert _published_context() is None


@pytest.mark.parametrize(
    ("trace_id", "span_id"),
    [
        (1, None),
        (None, 2),
        (0, 2),
        (1, 0),
        (1, 2**64),
    ],
)
def test_invalid_context_detaches_thread_context(tracer: Tracer, trace_id, span_id):
    tracer.context_provider.activate(DDContext(trace_id=1, span_id=2))
    assert _published_span_id() == 2

    tracer.context_provider.activate(DDContext(trace_id=trace_id, span_id=span_id))

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


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_CTX_ENABLED": "false"})
def test_thread_context_listeners_can_be_disabled():
    import sys

    assert "ddtrace" not in sys.modules

    from ddtrace.internal import core
    from ddtrace.internal.settings._config import config

    assert config._otel_thread_context_enabled is False
    assert core.has_listeners("ddtrace.context_provider.activate") is False
    assert core.has_listeners("python.context.switch") is False

    if sys.implementation.name == "cpython" and sys.version_info >= (3, 14):
        from ddtrace.internal.native._native import is_context_watcher_registered

        assert is_context_watcher_registered() is False


def test_python_context_switch_syncs_active_span(tracer: Tracer):
    with tracer.trace("test") as span:
        detach_otel_thread_context()
        assert _published_span_id() is None

        core.dispatch("python.context.switch")
        assert _published_span_id() == span.span_id

        Context().run(core.dispatch, "python.context.switch")
        # CPython's context watcher restores the outer context after Context.run().
        if sys.implementation.name == "cpython" and sys.version_info >= (3, 14):
            assert _published_span_id() == span.span_id
        else:
            assert _published_span_id() is None

        core.dispatch("python.context.switch")
        assert _published_span_id() == span.span_id


def test_python_context_switch_syncs_active_context(tracer: Tracer):
    context = DDContext(trace_id=123, span_id=456, sampling_priority=1)
    tracer.context_provider.activate(context)
    detach_otel_thread_context()

    core.dispatch("python.context.switch")

    assert _published_context() == (context.trace_id, context.span_id, 1, 0)


def test_asyncio_to_thread_syncs_thread_context(tracer: Tracer):
    """The OTel TLS record follows the active context copied into an asyncio worker thread."""

    async def exercise():
        with tracer.trace("parent") as parent:
            assert await asyncio.to_thread(_published_span_id) == parent.span_id
        assert await asyncio.to_thread(_published_span_id) is None

    was_patched = getattr(asyncio, "_datadog_patch", False)
    unpatch()
    patch()
    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(exercise())
    finally:
        loop.run_until_complete(loop.shutdown_default_executor())
        loop.close()
        unpatch()
        if was_patched:
            patch()


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
