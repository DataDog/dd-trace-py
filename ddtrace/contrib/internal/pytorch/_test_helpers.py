"""Test-only helpers for the PyTorch integration.

Previously exposed as ``_*_for_tests`` symbols on the production submodules;
moved here so the production modules don't carry test-only API surface.
Import as::

    from ddtrace.contrib.internal.pytorch import _test_helpers as th
"""

from typing import Any
from typing import Optional


def reset_metrics_state() -> None:
    pass


def current_rank_span() -> Optional[Any]:
    from ddtrace.contrib.internal.pytorch import _rank_root

    return _rank_root._span


def close_rank_root() -> None:
    """Force-close the rank-root span and reset module state (test isolation)."""
    from ddtrace.contrib.internal.pytorch import _rank_root

    with _rank_root._lock:
        span = _rank_root._span
        _rank_root._span = None
        _rank_root._atexit_registered = False
        timer = _rank_root._rotation_timer
        _rank_root._rotation_timer = None
        _rank_root._open_kwargs = {}
    if timer is not None:
        try:
            timer.cancel()
        except Exception:  # nosec B110
            pass
    if span is not None:
        try:
            span.finish()
        except Exception:  # nosec B110
            pass


def set_atexit_registered(value: bool) -> None:
    from ddtrace.contrib.internal.pytorch import _rank_root

    _rank_root._atexit_registered = value


def reset_device_cache() -> None:
    from ddtrace.contrib.internal.pytorch import _device

    _device._cache = None
