"""Test-only helpers for the PyTorch integration.

Previously exposed as ``_*_for_tests`` symbols on the production submodules;
moved here so the production modules don't carry test-only API surface.
Import as::

    from ddtrace.contrib.internal.pytorch import _test_helpers as th
    th.install_metrics_client(fake)
"""

from typing import Any
from typing import Optional


def install_metrics_client(client: Any) -> None:
    from ddtrace.contrib.internal.pytorch import _metrics

    _metrics._DOGSTATSD = client


def reset_metrics_state() -> None:
    from ddtrace.contrib.internal.pytorch import _metrics

    _metrics._DOGSTATSD = None
    with _metrics._counter_lock:
        _metrics._counters.clear()
        _metrics._durations.clear()


def metrics_ticker() -> Optional[Any]:
    """Return the live rate ticker handle (the one started by bootstrap),
    or None if no ticker is currently running.
    """
    from ddtrace.contrib.internal.pytorch import _distributed

    return _distributed._state.get("rate_ticker")


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
    if span is not None:
        try:
            span.finish()
        except Exception:
            pass


def set_atexit_registered(value: bool) -> None:
    from ddtrace.contrib.internal.pytorch import _rank_root

    _rank_root._atexit_registered = value


def reset_device_cache() -> None:
    from ddtrace.contrib.internal.pytorch import _device

    _device._cache = None
