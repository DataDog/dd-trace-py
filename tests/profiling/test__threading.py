"""Tests for ddtrace.profiling._threading utilities."""
from __future__ import annotations

import threading

from ddtrace.profiling import _threading


def test_get_thread_native_id_current_thread():
    """get_thread_native_id returns the native id for a normal thread."""
    tid = threading.current_thread().ident
    native_id = _threading.get_thread_native_id(tid)
    assert native_id == threading.current_thread().native_id


def test_get_thread_native_id_unknown_thread():
    """get_thread_native_id falls back to thread_id when thread is not found."""
    fake_tid = 0xDEADBEEF
    assert _threading.get_thread_native_id(fake_tid) == fake_tid


def test_get_thread_native_id_dummy_thread():
    """get_thread_native_id handles _DummyThread (no _native_id attr).

    gevent monkey-patches threading and registers greenlets as _DummyThread
    instances which lack _native_id, causing .native_id to raise AttributeError.
    Regression test for https://github.com/DataDog/dd-trace-py/issues/16745
    """
    dummy = threading._DummyThread()
    # Force _native_id to be missing so we reliably reproduce the gevent
    # scenario regardless of Python version.
    try:
        del dummy._native_id
    except AttributeError:
        pass

    # Temporarily register the dummy under a synthetic thread ID so
    # get_thread_by_id (a Cython cpdef) finds it via threading._active.
    fake_tid = 0xBADCAFE
    threading._active[fake_tid] = dummy
    try:
        result = _threading.get_thread_native_id(fake_tid)
    finally:
        del threading._active[fake_tid]

    assert result == fake_tid
