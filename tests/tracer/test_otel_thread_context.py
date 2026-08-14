import concurrent.futures
from contextvars import Context
import ctypes
import os
import sys
import threading

import pytest

from ddtrace._trace.context import SAMPLING_DECISION_EVENT
from ddtrace._trace.context import Context as DDContext
from ddtrace._trace.provider import DefaultContextProvider
from ddtrace._trace.tracer import Tracer
from ddtrace.internal import core
from ddtrace.internal.opentelemetry.thread_context import register_otel_thread_context_listener


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="OTel thread context is only published on Linux")


if sys.platform == "linux":
    from ddtrace.internal.native import _native
    from ddtrace.internal.native._native import detach_otel_thread_context

    # 28-byte header, then packed (key_index, len, value) attribute entries.
    _ROOT_SPAN_KEY_INDEX = 0

    class _ThreadContextRecord(ctypes.Structure):
        _fields_ = [
            ("trace_id", ctypes.c_ubyte * 16),
            ("span_id", ctypes.c_ubyte * 8),
            ("valid", ctypes.c_ubyte),
            ("trace_flags", ctypes.c_ubyte),
            ("attrs_data_size", ctypes.c_uint16),
            ("attrs_data", ctypes.c_ubyte * 612),
        ]

    _NATIVE_LIBRARY = ctypes.CDLL(_native.__file__)


def _record():
    slot = ctypes.c_void_p.in_dll(_NATIVE_LIBRARY, "otel_thread_ctx_v1")
    if slot.value is None:
        return None

    record = _ThreadContextRecord.from_address(slot.value)
    if not record.valid:
        return None
    return record


def _published_span_id():
    record = _record()
    if record is None:
        return None
    return int.from_bytes(record.span_id, byteorder="big")


def _published_trace_flags():
    record = _record()
    if record is None:
        return None
    return record.trace_flags


def _published_trace_id():
    record = _record()
    if record is None:
        return None
    return int.from_bytes(record.trace_id, byteorder="big")


def _published_local_root_span_id():
    record = _record()
    if record is None:
        return None
    blob = bytes(record.attrs_data[: record.attrs_data_size])
    offset = 0
    while offset + 2 <= len(blob):
        key, length = blob[offset], blob[offset + 1]
        value = blob[offset + 2 : offset + 2 + length]
        if len(value) < length:
            break
        if key == _ROOT_SPAN_KEY_INDEX:
            return int.from_bytes(value, byteorder="big")
        offset += 2 + length
    return None


@pytest.fixture(autouse=True)
def _register_otel_thread_context_listener(tracer):
    listeners = register_otel_thread_context_listener(tracer)
    assert listeners is not None
    activation_listener, resync_listener = listeners
    yield
    core.reset_listeners("ddtrace.context_provider.activate", activation_listener)
    core.reset_listeners("python.context.switch", resync_listener)
    core.reset_listeners(SAMPLING_DECISION_EVENT, resync_listener)


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


def test_span_publishes_local_root_span_id(tracer: Tracer):
    with tracer.trace("root") as root:
        assert _published_local_root_span_id() == root.span_id
        with tracer.trace("child") as child:
            assert _published_span_id() == child.span_id
            assert _published_local_root_span_id() == root.span_id


def test_active_context_is_published(tracer: Tracer):
    ctx = DDContext(trace_id=12345, span_id=678)
    with tracer._activate_context(ctx):
        assert _published_trace_id() == 12345
        assert _published_span_id() == 678
        # A Context's local root is not knowable, so the span stands in for it.
        assert _published_local_root_span_id() == 678

    assert _published_span_id() is None


def test_offloaded_work_publishes_the_submitting_span(tracer: Tracer):
    """A worker thread is attributable to the span that handed it the work.

    This is the handoff futures/threading.py performs on submit.
    """
    published = {}

    with tracer.trace("submitter") as submitter:
        handoff = submitter.context.copy(submitter.trace_id, submitter.span_id)

        def worker():
            with tracer._activate_context(handoff):
                published["trace_id"] = _published_trace_id()
                published["span_id"] = _published_span_id()

        thread = threading.Thread(target=worker)
        thread.start()
        thread.join()

        assert published["trace_id"] == submitter.trace_id
        assert published["span_id"] == submitter.span_id


def test_context_switch_does_not_repair_the_contextvar(tracer: Tracer):
    from ddtrace._trace.provider import _DD_CONTEXTVAR

    parent = DDContext(trace_id=1, span_id=2)
    parent._reactivate = True
    span = tracer.start_span("child", child_of=parent)
    span.finish()
    tracer.context_provider.activate(span)

    core.dispatch("python.context.switch")

    assert _published_span_id() == 2
    assert _DD_CONTEXTVAR.get() is span


def test_sampling_decision_republishes_trace_flags(tracer: Tracer):
    """Nothing re-activates the span here: the decision alone has to republish."""
    with tracer.trace("test") as span:
        assert _published_trace_flags() == 0

        span.context.sampling_priority = 2
        assert _published_trace_flags() == 1

        span.context.sampling_priority = -1
        assert _published_trace_flags() == 0

        span.context.sampling_priority = 1
        assert _published_trace_flags() == 1
        span.context.sampling_priority = None
        assert _published_trace_flags() == 0


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

    from ddtrace._trace.context import SAMPLING_DECISION_EVENT
    from ddtrace.internal import core
    from ddtrace.internal.settings._config import config

    assert config._otel_thread_context_enabled is False
    assert core.has_listeners("ddtrace.context_provider.activate") is False
    assert core.has_listeners("python.context.switch") is False
    assert core.has_listeners(SAMPLING_DECISION_EVENT) is False

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
