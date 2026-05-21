# -*- encoding: utf-8 -*-
"""Regression tests for ddtrace.internal._unpatched.

Ensures the "unpatched" primitive cache actually holds *real* OS primitives
even when gevent.monkey.patch_all() runs before ddtrace is imported.
See: releasenote fix-profiler-fork-gevent-late-import-*.yaml
"""
import pytest


@pytest.mark.subprocess(out="OK\n")
def test_unpatched_primitives_after_gevent_patch_all() -> None:
    """When gevent patches first, _unpatched must still hold OS-level primitives.

    This is the watchdog for the fix to the test_fork_gevent deadlock.
    If _unpatched.threading_Lock is a gevent semaphore, a native C++ periodic
    thread that tries to acquire one will block waiting for the gevent hub —
    which never runs because the main thread is parked in os.register_at_fork,
    causing an infinite deadlock.

    Note on what gevent actually patches (verified with gevent.monkey.saved):
      - threading.Lock    -> YES, replaced with gevent.thread.LockType
      - threading.Event   -> YES, replaced with gevent.event.Event
      - threading.RLock   -> NO, not replaced (only _allocate_lock underneath is replaced)
      - _thread.allocate_lock -> YES, replaced
      - _thread.RLock     -> NO, not replaced
    """
    import gevent.monkey

    gevent.monkey.patch_all()

    # Confirm gevent actually patched threading.Lock so this test is meaningful.
    import threading

    assert "gevent" in type(threading.Lock()).__module__, (
        "gevent did not patch threading.Lock — test precondition failed"
    )

    # Now import ddtrace internals — happens AFTER patch_all, the hostile ordering.
    from ddtrace.internal import _unpatched
    from ddtrace.internal import threads as _threads

    # Recover originals that gevent stashed away.
    saved_threading = gevent.monkey.saved.get("threading", {})
    saved_thread = gevent.monkey.saved.get("_thread", {})

    # threading.Lock IS patched by gevent. _unpatched must hold the original.
    original_Lock = saved_threading.get("Lock")
    assert original_Lock is not None, "gevent did not save threading.Lock original — check gevent version"
    assert _unpatched.threading_Lock is original_Lock, (
        f"_unpatched.threading_Lock is {_unpatched.threading_Lock!r}, "
        f"expected original {original_Lock!r}"
    )

    # threading.Event IS patched by gevent.
    original_Event = saved_threading.get("Event")
    assert original_Event is not None, "gevent did not save threading.Event original — check gevent version"
    assert _unpatched.threading_Event is original_Event, (
        f"_unpatched.threading_Event is {_unpatched.threading_Event!r}, "
        f"expected original {original_Event!r}"
    )

    # threading.RLock is NOT patched by gevent (only _allocate_lock underneath is).
    # Confirm _unpatched.threading_RLock is not a gevent type.
    assert "gevent" not in _unpatched.threading_RLock.__module__, (
        f"_unpatched.threading_RLock has gevent module ({_unpatched.threading_RLock.__module__})"
    )

    # _thread.allocate_lock IS patched by gevent.
    original_allocate_lock = saved_thread.get("allocate_lock")
    assert original_allocate_lock is not None, (
        "gevent did not save _thread.allocate_lock original — check gevent version"
    )
    assert _unpatched.unpatched_allocate_lock is original_allocate_lock, (
        f"_unpatched.unpatched_allocate_lock is {_unpatched.unpatched_allocate_lock!r}, "
        f"expected original {original_allocate_lock!r}"
    )

    # _forking_lock in threads.py must not be a gevent lock.
    lock_module = type(_threads._forking_lock).__module__
    assert "gevent" not in lock_module, (
        f"threads._forking_lock is a gevent type ({lock_module}); "
        "this will deadlock in os.register_at_fork before-hook"
    )

    print("OK")
