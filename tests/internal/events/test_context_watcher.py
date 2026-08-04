import contextvars
import sys
import threading

import pytest

from ddtrace.internal import core


pytestmark = pytest.mark.skipif(
    sys.implementation.name != "cpython" or sys.version_info < (3, 14),
    reason="requires the CPython 3.14 context watcher",
)


@pytest.fixture(autouse=True)
def _register_context_watcher():
    from ddtrace.internal.native._native import is_context_watcher_registered
    from ddtrace.internal.native._native import register_context_watcher

    assert register_context_watcher()
    assert is_context_watcher_registered()


def test_context_watcher_dispatches_events_and_releases_listener_snapshot():
    value = contextvars.ContextVar("value", default="outer")
    inner_context = contextvars.copy_context()
    inner_context.run(value.set, "inner")
    observed = []
    test_thread_id = threading.get_ident()

    def record_context_switch():
        if threading.get_ident() == test_thread_id:
            observed.append(value.get())

    core.on("python.context.switch", record_context_switch)
    try:
        reference_count = sys.getrefcount(record_context_switch)
        inner_context.run(lambda: None)
        assert sys.getrefcount(record_context_switch) == reference_count
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)

    assert observed == ["inner", "outer"]


def test_context_watcher_preserves_pending_exception_over_listener_failure():
    inner_context = contextvars.copy_context()
    observed = []
    unraisable = []
    test_thread_id = threading.get_ident()

    class ExpectedError(Exception):
        pass

    class ListenerError(BaseException):
        pass

    def record_context_switch():
        if threading.get_ident() != test_thread_id:
            return
        observed.append(contextvars.copy_context())
        if len(observed) == 2:
            raise ListenerError

    def raise_expected_error():
        raise ExpectedError

    original_unraisablehook = sys.unraisablehook
    sys.unraisablehook = lambda args: unraisable.append(args.exc_value)
    core.on("python.context.switch", record_context_switch)
    try:
        with pytest.raises(ExpectedError):
            inner_context.run(raise_expected_error)
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)
        sys.unraisablehook = original_unraisablehook

    assert len(observed) == 2
    assert len(unraisable) == 1
    assert isinstance(unraisable[0], ListenerError)


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "false"})
def test_context_watcher_slot_exhaustion_disables_watcher():
    import ctypes
    import sys

    assert "ddtrace.internal.native._native" not in sys.modules

    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint, ctypes.py_object)
    callback = callback_type(lambda event, obj: 0)
    add_watcher = ctypes.pythonapi.PyContext_AddWatcher
    add_watcher.argtypes = [callback_type]
    add_watcher.restype = ctypes.c_int
    clear_watcher = ctypes.pythonapi.PyContext_ClearWatcher
    clear_watcher.argtypes = [ctypes.c_int]
    clear_watcher.restype = ctypes.c_int

    watcher_ids = []
    for _ in range(64):
        try:
            watcher_ids.append(add_watcher(callback))
        except RuntimeError:
            break
    else:
        raise AssertionError("context watcher slots were not exhausted")

    try:
        from ddtrace.internal.native._native import is_context_watcher_registered
        from ddtrace.internal.native._native import register_context_watcher

        assert register_context_watcher() is False
        assert is_context_watcher_registered() is False
    finally:
        for watcher_id in watcher_ids:
            assert clear_watcher(watcher_id) == 0

    assert register_context_watcher() is False
    assert is_context_watcher_registered() is False


@pytest.mark.subprocess(env={"_DD_GLOBAL_TRACER_INIT": "false"})
def test_context_watcher_registration_is_idempotent():
    from contextvars import Context
    import importlib
    import sys

    from ddtrace.internal import core

    module_name = "ddtrace.internal.native._native"
    native = importlib.import_module(module_name)
    assert native.is_context_watcher_registered() is False
    assert native.register_context_watcher() is True
    assert native.is_context_watcher_registered() is True

    for _ in range(16):
        del sys.modules[module_name]
        native = importlib.import_module(module_name)
        assert native.register_context_watcher() is True
        assert native.is_context_watcher_registered() is True

    observed = []
    core.on("python.context.switch", lambda: observed.append(None))
    Context().run(lambda: None)
    assert observed == [None, None]
