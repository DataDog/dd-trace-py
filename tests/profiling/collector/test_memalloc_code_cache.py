"""Correctness tests for the PyCodeObject* -> function_id cache.

Covers:
  * cache populates while heap profiling
  * code_cache_reset_counters() zeros counters
  * code_cache_disable() / code_cache_enable() toggle
  * postfork_child clears cache slots (inherited PyCodeObject* otherwise hit)
  * eviction fires when active set exceeds capacity
"""

from __future__ import annotations

import os
import sys
import textwrap
from typing import TYPE_CHECKING
from typing import Callable

import pytest


if TYPE_CHECKING:
    # For type checking, use the real module so annotations like
    # _memalloc._CodeCacheStats resolve against the .pyi stub.
    from ddtrace.profiling.collector import _memalloc
else:
    # Skip on free-threaded builds before importing _memalloc: the extension
    # isn't available there and a module-level import would fail at collection
    # time. importorskip keeps collection from hard-failing on builds without
    # the extension.
    if hasattr(sys, "flags") and getattr(sys.flags, "no_gil", False):  # pragma: no cover
        pytest.skip("memalloc not supported on free-threaded Python", allow_module_level=True)
    _memalloc = pytest.importorskip("ddtrace.profiling.collector._memalloc")


MAX_FRAMES = 32
HEAP_SAMPLE_SIZE = 256
ALLOC_BYTES = 1024
PY_313_OR_ABOVE = sys.version_info[:2] >= (3, 13)


def _alloc_one(size: int) -> object:
    # Python 3.13 moved bytearray's buffer to an allocation domain the heap
    # profiler doesn't track; use a tuple (OBJ domain) on 3.13+ so allocations
    # stay tracked across versions.
    return (None,) * size if PY_313_OR_ABOVE else bytearray(size)


def _alloc_burst(n: int = 200) -> list[object]:
    """Allocate enough to almost certainly trigger a sample."""
    return [_alloc_one(ALLOC_BYTES) for _ in range(n)]


def _start() -> None:
    _memalloc.start(MAX_FRAMES, HEAP_SAMPLE_SIZE, True)


def _stop() -> None:
    _memalloc.stop()


def _stats() -> _memalloc._CodeCacheStats:
    s = _memalloc.code_cache_stats()
    assert s is not None
    return s


@pytest.fixture(autouse=True)
def _ensure_clean_start_stop():
    """Make sure each test starts with a clean memalloc state and the cache
    enabled at its default capacity (in case a prior test disabled it).
    """
    _memalloc.code_cache_enable()
    yield
    try:
        _memalloc.stop()
    except RuntimeError:
        pass
    _memalloc.code_cache_enable()


def test_code_cache_populates_during_sampling() -> None:
    _start()
    try:
        objs = _alloc_burst(500)
        stats = _stats()
        assert stats["hits"] + stats["misses"] > 0, "cache should have been consulted"
        assert stats["capacity"] > 0
        assert len(objs) == 500
    finally:
        _stop()


def test_code_cache_reset_counters() -> None:
    _start()
    try:
        _alloc_burst(500)
        before = _stats()
        assert before["hits"] + before["misses"] > 0
        _memalloc.code_cache_reset_counters()
        after = _stats()
        assert after["hits"] == 0
        assert after["misses"] == 0
        assert after["evictions"] == 0
        assert after["capacity"] == before["capacity"]
    finally:
        _stop()


def test_code_cache_per_set_stats_basic() -> None:
    """Return a histogram[k] for k in [0..WAYS_PER_SET]: sum equals num_sets,
    and an empty cache puts everything in bucket 0.
    """
    # Empty cache: disable() tears down the singleton (frees the backing vector),
    # then enable() reallocates a fresh one, so every slot starts empty.
    _memalloc.code_cache_disable()
    _memalloc.code_cache_enable()
    stats = _stats()
    num_sets = stats["capacity"] // 2  # WAYS_PER_SET == 2
    hist = _memalloc.code_cache_per_set_stats()
    assert hist is not None
    assert len(hist) == 3
    assert sum(hist) == num_sets
    assert hist[0] == num_sets
    assert all(hist[k] == 0 for k in range(1, 3))

    _start()
    try:
        _alloc_burst(500)
        hist = _memalloc.code_cache_per_set_stats()
        assert hist is not None
        assert len(hist) == 3
        assert sum(hist) == num_sets
        # At least one set should now hold an entry.
        assert any(hist[k] > 0 for k in range(1, 3))
    finally:
        _stop()

    # Disabled cache returns None.
    _memalloc.code_cache_disable()
    assert _memalloc.code_cache_per_set_stats() is None
    _memalloc.code_cache_enable()


def test_code_cache_disable_then_stats_is_none() -> None:
    _start()
    try:
        _alloc_burst(50)
        assert _memalloc.code_cache_stats() is not None
        _memalloc.code_cache_disable()
        assert _memalloc.code_cache_stats() is None
        # Allocations during disabled state must still work (slow path).
        _alloc_burst(50)
        _memalloc.code_cache_enable()
        assert _memalloc.code_cache_stats() is not None
    finally:
        _stop()


def test_code_cache_eviction_under_churn() -> None:
    """Force a small cache (cap=64) and allocate from far more distinct
    PyCodeObjects than fit; evictions must fire.
    """
    _memalloc.code_cache_disable()
    _memalloc.code_cache_enable(64)
    stats = _stats()
    assert stats["capacity"] == 64

    # Build 400 distinct functions (way over cap). Allocate a tracked type
    # ((None,)*N on 3.13+, bytearray otherwise) so samples fire across versions.
    alloc_expr = f"(None,) * {ALLOC_BYTES}" if PY_313_OR_ABOVE else f"bytearray({ALLOC_BYTES})"
    fns: list[Callable[[], object]] = []
    for i in range(400):
        ns: dict[str, Callable[[], object]] = {}
        exec(textwrap.dedent(f"def fn_{i}(): return {alloc_expr}"), ns)
        fns.append(ns[f"fn_{i}"])

    _start()
    try:
        _memalloc.code_cache_reset_counters()
        # Call each fn many times to spread allocations and ensure several
        # sampling events from each.
        for _ in range(3):
            for fn in fns:
                fn()
        stats = _stats()
        assert stats["evictions"] > 0, f"expected evictions with cap=64 and 400 distinct fns, got stats={stats}"
    finally:
        _stop()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="os.fork is not available on this platform")
def test_code_cache_cleared_on_postfork_child() -> None:
    """After fork, the child inherits the parent's PyCodeObject* values
    but the cache slots must be empty. Verify by: parent populates cache,
    child resets counters and allocates -- on a cleared cache the lookups
    miss; on a NON-cleared cache the inherited entries would hit because
    PyCodeObject* addresses survive fork unchanged.
    """
    _start()
    try:
        # Parent: populate the cache.
        _alloc_burst(500)
        parent = _stats()
        assert parent["hits"] > 50, f"parent cache not warm enough: {parent}"

        # Pipe so child can report its post-fork stats.
        r, w = os.pipe()
        pid = os.fork()
        if pid == 0:
            try:
                os.close(r)
                # postfork_child has run -- slots should be empty.
                _memalloc.code_cache_reset_counters()
                _alloc_burst(500)
                child_stats = _memalloc.code_cache_stats()
                hits_v = child_stats["hits"] if child_stats is not None else 0
                misses_v = child_stats["misses"] if child_stats is not None else 0
                payload = f"{hits_v}|{misses_v}".encode()
                os.write(w, payload)
                os.close(w)
            finally:
                # Bypass normal teardown so parent's heap tracker doesn't see
                # child-side stop side effects.
                os._exit(0)
        else:
            os.close(w)
            data = os.read(r, 256).decode()
            os.close(r)
            _, status = os.waitpid(pid, 0)
            assert not os.WIFSIGNALED(status), f"child crashed: signal {os.WTERMSIG(status)}"
            assert os.WEXITSTATUS(status) == 0
            hits_str, misses_str = data.split("|")
            child_hits = int(hits_str)
            child_misses = int(misses_str)
            # If postfork_child cleared the cache, the child's first lookups
            # must miss. If it did NOT clear, the inherited entries (same
            # PyCodeObject* addresses after fork) would all hit.
            assert child_misses > 0, (
                f"child saw no misses, suggesting cache wasn't cleared (hits={child_hits}, misses={child_misses})"
            )
    finally:
        _stop()
