"""Tests for ddtrace.profiling._threading utilities."""

from __future__ import annotations

import threading
from typing import Optional

from ddtrace.profiling._threading import get_thread_native_id


def test_get_thread_native_id_current_thread() -> None:
    """get_thread_native_id returns the native id for a normal thread."""
    tid: Optional[int] = threading.current_thread().ident
    assert tid is not None, "current_thread().ident should never be None for a running thread"  # nosec B101
    native_id: int = get_thread_native_id(tid)
    expected: Optional[int] = getattr(
        threading.current_thread(), "native_id", tid
    )  # fallback to tid if current thread is a _DummyThread
    assert native_id == expected  # nosec B101


def test_get_thread_native_id_unknown_thread() -> None:
    """get_thread_native_id falls back to thread_id when thread is not found."""
    fake_tid: int = 0xDEADBEEF
    assert get_thread_native_id(fake_tid) == fake_tid  # nosec B101


def test_get_thread_native_id_dummy_thread() -> None:
    """get_thread_native_id handles _DummyThread (no _native_id attr).

    _DummyThread instances lack _native_id, causing .native_id to raise
    AttributeError. This can occur with threads not created via threading.Thread
    (e.g., greenlets, C-level threads, _thread.start_new_thread).
    Regression test for https://github.com/DataDog/dd-trace-py/issues/16745
    """
    # _DummyThread.__init__ calls _set_ident() + _active[self._ident] = self,
    # which overwrites the real main-thread entry.  Save and restore it so
    # later tests still see "MainThread".
    main_ident: int = threading.get_ident()
    main_thread_obj: Optional[threading.Thread] = threading._active.get(main_ident)  # type: ignore[attr-defined]

    fake_tid: int = 0xBADCAFE
    try:
        dummy: threading.Thread = threading._DummyThread()
        if hasattr(dummy, "_native_id"):
            del dummy._native_id

        threading._active[fake_tid] = dummy  # type: ignore[attr-defined]
        assert get_thread_native_id(fake_tid) == fake_tid  # nosec B101
    finally:
        threading._active.pop(fake_tid, None)  # type: ignore[attr-defined]
        if main_thread_obj is not None:
            threading._active[main_ident] = main_thread_obj  # type: ignore[attr-defined]
