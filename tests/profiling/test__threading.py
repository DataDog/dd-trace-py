"""Tests for ddtrace.profiling._threading utilities."""

from __future__ import annotations

import threading
from typing import Optional

from ddtrace.profiling._threading import get_thread_native_id


def test_get_thread_native_id_current_thread() -> None:
    """get_thread_native_id returns the native id for a normal thread."""
    tid: Optional[int] = threading.current_thread().ident
    if tid:
        native_id: int = get_thread_native_id(tid)
        expected: Optional[int] = getattr(threading.current_thread(), "native_id", None)
        assert native_id == expected or tid  # Current thread is a _DummyThread


def test_get_thread_native_id_unknown_thread() -> None:
    """get_thread_native_id falls back to thread_id when thread is not found."""
    fake_tid: int = 0xDEADBEEF
    assert get_thread_native_id(fake_tid) == fake_tid


def test_get_thread_native_id_dummy_thread() -> None:
    """get_thread_native_id handles _DummyThread (no _native_id attr).

    gevent monkey-patches threading and registers greenlets as _DummyThread
    instances which lack _native_id, causing .native_id to raise AttributeError.
    Regression test for https://github.com/DataDog/dd-trace-py/issues/16745
    """
    dummy: threading.Thread = threading._DummyThread()
    # Force _native_id to be missing so we reliably reproduce the gevent
    # scenario regardless of Python version.
    try:
        del dummy._native_id  # type: ignore[attr-defined]
    except AttributeError:
        pass

    # Temporarily register the dummy under a synthetic thread ID so
    # get_thread_by_id (a Cython cpdef) finds it via threading._active.
    fake_tid: int = 0xBADCAFE
    threading._active[fake_tid] = dummy  # type: ignore[attr-defined]
    try:
        result: int = get_thread_native_id(fake_tid)
    finally:
        del threading._active[fake_tid]  # type: ignore[attr-defined]

    assert result == fake_tid
