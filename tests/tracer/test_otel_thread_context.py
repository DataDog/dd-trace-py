import ctypes
import sys

import pytest


pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="OTel thread context is only published on Linux")


if sys.platform == "linux":
    from ddtrace.internal.native import _native

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


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_CTX_ENABLED": "true"})
def test_span_context_is_published_and_detached():
    from tests.tracer.test_otel_thread_context import _published_span_id
    from tests.utils import scoped_tracer

    with scoped_tracer() as tracer:
        with tracer.trace("test") as span:
            assert _published_span_id() == span.span_id

        assert _published_span_id() is None


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_CTX_ENABLED": "true"})
def test_span_context_is_thread_local():
    import concurrent.futures
    import threading

    from tests.tracer.test_otel_thread_context import _published_span_id
    from tests.utils import scoped_tracer

    barrier = threading.Barrier(2)

    def trace(name):
        with tracer.trace(name) as span:
            barrier.wait()
            return span.span_id, _published_span_id()

    with scoped_tracer() as tracer:
        # In CI, scoped_tracer forwards traces to a NativeWriter. Start it before the workers so its unrelated
        # lazy-start race does not obscure the thread-local context behavior this test exercises.
        with tracer.trace("writer-warmup"):
            pass

        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
            results = executor.map(trace, ("one", "two"))

        assert all(span_id == published_span_id for span_id, published_span_id in results)


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_CTX_ENABLED": "true"})
def test_only_installed_context_provider_updates_thread_context():
    from ddtrace._trace.provider import DefaultContextProvider
    from tests.tracer.test_otel_thread_context import _published_span_id
    from tests.utils import scoped_tracer

    uninstalled_provider = DefaultContextProvider()

    with scoped_tracer() as tracer:
        with tracer.trace("test") as span:
            uninstalled_provider.activate(None)

            assert _published_span_id() == span.span_id


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_CTX_ENABLED": None})
def test_thread_context_listeners_are_disabled_by_default():
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


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_CTX_ENABLED": "true"})
def test_python_context_switch_syncs_active_span():
    from contextvars import Context
    import sys

    from ddtrace.internal import core
    from ddtrace.internal.native._native import detach_otel_thread_context
    from tests.tracer.test_otel_thread_context import _published_span_id
    from tests.utils import scoped_tracer

    with scoped_tracer() as tracer:
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


@pytest.mark.subprocess(env={"DD_TRACE_OTEL_CTX_ENABLED": "true"})
def test_span_context_is_reactivated_after_fork():
    import os
    import sys

    from ddtrace.internal.native._native import detach_otel_thread_context
    from tests.tracer.test_otel_thread_context import _published_span_id
    from tests.utils import scoped_tracer

    with scoped_tracer() as tracer:
        with tracer.trace("test") as span:
            if sys.platform == "linux":  # to satisfy the type checker outside of linux
                detach_otel_thread_context()
            pid = os.fork()
            if pid == 0:
                os._exit(0 if _published_span_id() == span.span_id else 1)

            tracer.context_provider.activate(span)
            _, status = os.waitpid(pid, 0)

        assert os.waitstatus_to_exitcode(status) == 0
