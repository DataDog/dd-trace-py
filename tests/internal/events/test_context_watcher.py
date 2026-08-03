import contextvars
import sys

import pytest

from ddtrace.internal import core


@pytest.mark.skipif(
    sys.implementation.name != "cpython" or sys.version_info < (3, 14),
    reason="requires the CPython 3.14 context watcher",
)
def test_context_watcher_dispatches_context_switch_events():
    value = contextvars.ContextVar("value", default="outer")
    inner_context = contextvars.copy_context()
    inner_context.run(value.set, "inner")
    observed = []

    def record_context_switch():
        observed.append(value.get())

    core.on("python.context.switch", record_context_switch)
    try:
        inner_context.run(lambda: None)
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)

    assert observed == ["inner", "outer"]


@pytest.mark.skipif(
    sys.implementation.name != "cpython" or sys.version_info < (3, 14),
    reason="requires the CPython 3.14 context watcher",
)
def test_context_watcher_preserves_pending_exception():
    inner_context = contextvars.copy_context()
    observed = []

    class ExpectedError(Exception):
        pass

    def record_context_switch():
        observed.append(contextvars.copy_context())

    def raise_expected_error():
        raise ExpectedError

    core.on("python.context.switch", record_context_switch)
    try:
        with pytest.raises(ExpectedError):
            inner_context.run(raise_expected_error)
    finally:
        core.reset_listeners("python.context.switch", record_context_switch)

    assert len(observed) == 2


@pytest.mark.skipif(
    sys.implementation.name != "cpython" or sys.version_info < (3, 14),
    reason="requires the CPython 3.14 context watcher",
)
def test_context_watcher_slot_exhaustion_does_not_break_import(run_python_code_in_subprocess):
    stdout, stderr, status, _ = run_python_code_in_subprocess(
        """
import ctypes
import sys

assert "ddtrace.internal.native._native" not in sys.modules

callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_uint, ctypes.py_object)
callback = callback_type(lambda event, obj: 0)
add_watcher = ctypes.pythonapi.PyContext_AddWatcher
add_watcher.argtypes = [callback_type]
add_watcher.restype = ctypes.c_int

for _ in range(8):
    assert add_watcher(callback) >= 0

import ddtrace.internal.native._native
"""
    )

    assert status == 0, stderr.decode()
    assert stdout == b""
