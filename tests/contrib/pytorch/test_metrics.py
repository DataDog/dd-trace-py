"""Layer Zero metric reservoir for the PyTorch contrib integration.

Tests cover the in-memory duration reservoir that feeds the
``collective.<op>.{p10,p50,p90,p99,n}_ms`` facets on the rotated
``pytorch.rank`` span via ``summary_snapshot_and_reset()``.

DogStatsD emission and the RateTicker background thread were removed in
the pytorch-remove-layer-zero-dogstatsd change. Tests for those paths
have been deleted accordingly.
"""

from unittest import mock

import pytest

from ddtrace.contrib.internal.pytorch import _device
from ddtrace.contrib.internal.pytorch import _metrics
from ddtrace.contrib.internal.pytorch import _test_helpers as _th


@pytest.fixture(autouse=True)
def _reset():
    _th.reset_device_cache()
    _th.reset_metrics_state()
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-1"),
    ):
        _device.discover(local_rank=0)
    yield
    _th.reset_device_cache()
    _th.reset_metrics_state()


def test_record_collective_noop_before_device_discover():
    _th.reset_device_cache()
    _metrics.record_collective(op="allreduce", duration_ms=1.0, bytes_count=128)
    assert _metrics.summary_snapshot_and_reset() == {}


# ---------------------------------------------------------------------------
# Per-op duration reservoirs / summary snapshot for the pytorch.rank tag
# ---------------------------------------------------------------------------


def test_record_collective_buffers_duration_samples():
    """record_collective must push duration_ms into the per-op reservoir so a
    later summary_snapshot picks them up.
    """
    _metrics.record_collective(op="allreduce", duration_ms=0.4, bytes_count=64)
    _metrics.record_collective(op="allreduce", duration_ms=0.6, bytes_count=64)
    summary = _metrics.summary_snapshot_and_reset()
    assert "allreduce" in summary
    assert summary["allreduce"]["n"] == 2


def test_summary_snapshot_computes_percentiles_from_known_samples():
    """Given 100 samples drawn from a known distribution, the percentiles
    should match the obvious values (nearest-rank).
    """
    # 100 samples uniformly 0.01..1.00
    for i in range(1, 101):
        _metrics.record_collective(op="allreduce", duration_ms=i / 100.0, bytes_count=1)
    summary = _metrics.summary_snapshot_and_reset()
    s = summary["allreduce"]
    assert s["n"] == 100
    # Nearest-rank: p10 = sample at index 10, etc. Allow small tolerance for
    # rank-rounding (different valid formulas yield index 9 vs 10).
    assert s["p10"] == pytest.approx(0.10, abs=0.02)
    assert s["p50"] == pytest.approx(0.50, abs=0.02)
    assert s["p90"] == pytest.approx(0.90, abs=0.02)
    # p99 sits squarely in the tail — that's the whole point of including
    # it for straggler detection.
    assert s["p99"] == pytest.approx(0.99, abs=0.02)


def test_summary_snapshot_groups_by_op():
    for _ in range(5):
        _metrics.record_collective(op="allreduce", duration_ms=0.3, bytes_count=1)
    for _ in range(7):
        _metrics.record_collective(op="grad_comm", duration_ms=0.5, bytes_count=1)
    summary = _metrics.summary_snapshot_and_reset()
    assert summary["allreduce"]["n"] == 5
    assert summary["grad_comm"]["n"] == 7


def test_summary_snapshot_resets_reservoirs():
    _metrics.record_collective(op="allreduce", duration_ms=0.3, bytes_count=1)
    _metrics.summary_snapshot_and_reset()
    assert _metrics.summary_snapshot_and_reset() == {}


def test_summary_snapshot_returns_empty_when_no_samples():
    assert _metrics.summary_snapshot_and_reset() == {}


def test_reservoir_is_bounded_but_n_tracks_total():
    """Hot-path safety: the in-memory sample buffer must stay bounded so a
    long window with millions of collectives doesn't grow memory without
    limit. Meanwhile `n` keeps the *observation* count — that's what
    investigators read to compare workload across ranks.
    """
    cap = _metrics._RESERVOIR_MAX
    total = cap * 3
    for i in range(total):
        _metrics.record_collective(op="allreduce", duration_ms=float(i), bytes_count=1)
    # Internal: reservoir capped at _RESERVOIR_MAX.
    with _metrics._counter_lock:
        observed, buf = _metrics._durations["allreduce"]
    assert len(buf) == cap, "reservoir grew past the cap"
    assert observed == total
    # External: `n` in the summary mirrors the observation count.
    summary = _metrics.summary_snapshot_and_reset()
    assert summary["allreduce"]["n"] == total


def test_record_collective_noop_before_device_buffers_nothing():
    """Reservoir state must follow the same gating as the former
    counters/distributions — pre-bootstrap samples are dropped so they can't
    pollute the first post-bootstrap rank-span summary.
    """
    _th.reset_device_cache()
    _metrics.record_collective(op="allreduce", duration_ms=0.5, bytes_count=1)
    assert _metrics.summary_snapshot_and_reset() == {}


def test_record_collective_accepts_bytes_count_without_error():
    """bytes_count is accepted for source compatibility but not stored."""
    _metrics.record_collective(op="allreduce", duration_ms=1.0, bytes_count=999999)
    summary = _metrics.summary_snapshot_and_reset()
    # Duration still recorded normally.
    assert summary["allreduce"]["n"] == 1
