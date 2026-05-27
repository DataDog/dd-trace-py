"""Layer Zero metric emission via DogStatsD."""

import threading
import time
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


def test_record_collective_emits_two_distributions():
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    _metrics.record_collective(op="allreduce", duration_ms=2.5, bytes_count=4096)
    assert fake.distribution.call_count == 2
    names = [call.args[0] for call in fake.distribution.call_args_list]
    # The `namespace="pytorch"` prefix is applied by the real DogStatsd
    # vendor client at send time, not by us. With a mocked client we see
    # the un-namespaced names that `record_collective` actually passes.
    assert "collective.duration_ms" in names
    assert "collective.bytes" in names


def test_record_collective_tags_include_device_and_op():
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    _metrics.record_collective(op="allgather", duration_ms=1.0, bytes_count=128)
    for call in fake.distribution.call_args_list:
        tags = call.kwargs.get("tags") or (call.args[2] if len(call.args) > 2 else None)
        assert "op:allgather" in tags
        assert any(t.startswith("device.id:") for t in tags)


def test_record_collective_tags_exclude_job_and_rank():
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    _metrics.record_collective(op="broadcast", duration_ms=1.0, bytes_count=128)
    for call in fake.distribution.call_args_list:
        tags = call.kwargs.get("tags") or (call.args[2] if len(call.args) > 2 else None)
        assert not any(t.startswith("training_job.id:") for t in tags)
        assert not any(t.startswith("rank:") for t in tags)


def test_record_collective_noop_before_device_discover():
    _th.reset_device_cache()
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    _metrics.record_collective(op="allreduce", duration_ms=1.0, bytes_count=128)
    assert fake.distribution.call_count == 0
    # AIDEV-NOTE: counters must NOT accumulate either; otherwise the first
    # post-bootstrap ticker tick would attribute pre-bootstrap traffic to
    # the post-bootstrap device tag.
    assert _metrics._snapshot_and_reset_counters() == {}


def test_counters_accumulate_by_op():
    _metrics.record_collective(op="allreduce", duration_ms=1.0, bytes_count=100)
    _metrics.record_collective(op="allreduce", duration_ms=1.0, bytes_count=300)
    _metrics.record_collective(op="broadcast", duration_ms=1.0, bytes_count=50)
    snap = _metrics._snapshot_and_reset_counters()
    assert snap[("allreduce",)] == (2, 400)
    assert snap[("broadcast",)] == (1, 50)


def test_snapshot_resets_counters():
    _metrics.record_collective(op="allreduce", duration_ms=1.0, bytes_count=100)
    _metrics._snapshot_and_reset_counters()
    snap = _metrics._snapshot_and_reset_counters()
    assert snap == {}


def test_ticker_first_tick_discarded(monkeypatch):
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    ticker = _metrics.RateTicker(interval_s=0.05)
    ticker.start()
    try:
        _metrics.record_collective(op="allreduce", duration_ms=1.0, bytes_count=4096)
        time.sleep(0.12)
    finally:
        ticker.stop()
    rate_metric_names = {
        c.args[0]
        for c in fake.distribution.call_args_list
        if c.args[0] in ("collective.bytes_per_sec", "collective.calls_per_sec")
    }
    assert rate_metric_names == {"collective.bytes_per_sec", "collective.calls_per_sec"}


def test_ticker_thread_safe_under_concurrent_record():
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    ticker = _metrics.RateTicker(interval_s=0.02)
    stop = threading.Event()

    def hammer():
        while not stop.is_set():
            _metrics.record_collective(op="allreduce", duration_ms=0.1, bytes_count=1)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    ticker.start()
    for t in threads:
        t.start()
    time.sleep(0.15)
    stop.set()
    for t in threads:
        t.join(timeout=1)
    ticker.stop()


def test_ticker_stop_joins_within_timeout():
    ticker = _metrics.RateTicker(interval_s=0.05)
    ticker.start()
    t0 = time.perf_counter()
    ticker.stop(timeout=0.5)
    assert time.perf_counter() - t0 < 0.5
    assert not ticker._thread.is_alive()


def test_rate_ticker_enters_backoff_after_consecutive_failures():
    """After ``_FAILURE_BACKOFF_THRESHOLD`` consecutive ``distribution`` raises,
    the ticker should set ``_backoff_until_ns`` and stop attempting sends until
    the cool-down expires.
    """
    fake = mock.Mock()
    fake.distribution.side_effect = RuntimeError("dogstatsd down")
    _th.install_metrics_client(fake)
    ticker = _metrics.RateTicker(interval_s=0.0)
    try:
        for _ in range(_metrics._FAILURE_BACKOFF_THRESHOLD):
            _metrics.record_collective(op="allreduce", duration_ms=0.1, bytes_count=64)
            ticker._emit_tick()
        assert ticker._backoff_until_ns > time.perf_counter_ns(), "backoff window not set"
        # After backoff is set, the consecutive_failures counter is reset to
        # 0 so a still-broken agent can trip the threshold again post-window.
        assert ticker._consecutive_failures == 0
    finally:
        ticker.stop(timeout=0.1)


def test_rate_ticker_drops_counters_during_backoff(monkeypatch):
    """Counters accumulated during the backoff window must not be carried
    over into the first post-recovery tick — that would inflate
    ``calls_per_sec`` by ~window_seconds / interval_seconds.
    """
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    # Bump counters directly so the per-event distribution emission doesn't
    # touch the fake mock (we only care about ticker behaviour here).
    for _ in range(50):
        _metrics._bump_counters("allreduce", 64)

    ticker = _metrics.RateTicker(interval_s=0.0)
    # Force an active backoff window.
    ticker._backoff_until_ns = time.perf_counter_ns() + int(60 * 1e9)
    try:
        # While in backoff, _emit_tick should drain counters and skip emission.
        ticker._emit_tick()
        rate_calls = [
            c
            for c in fake.distribution.call_args_list
            if c.args[0] in ("collective.calls_per_sec", "collective.bytes_per_sec")
        ]
        assert rate_calls == [], "rate-style distribution called during backoff"
        # Counters should now be empty (drained by the backoff tick).
        assert _metrics._snapshot_and_reset_counters() == {}
    finally:
        ticker.stop(timeout=0.1)


def test_rate_ticker_recovers_after_backoff_window():
    """When the backoff window expires and the agent comes back, the ticker
    should resume sending and reset its failure counter.
    """
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    ticker = _metrics.RateTicker(interval_s=0.0)
    # Pretend we just emerged from a backoff window.
    ticker._backoff_until_ns = time.perf_counter_ns() - 1
    ticker._consecutive_failures = _metrics._FAILURE_BACKOFF_THRESHOLD
    try:
        _metrics.record_collective(op="allreduce", duration_ms=0.1, bytes_count=64)
        ticker._emit_tick()
        # A good tick resets the failure counter and emits distributions.
        assert ticker._consecutive_failures == 0
        assert fake.distribution.called
    finally:
        ticker.stop(timeout=0.1)


# ---------------------------------------------------------------------------
# Per-op duration reservoirs / summary snapshot for the pytorch.rank tag
# ---------------------------------------------------------------------------


def test_record_collective_buffers_duration_samples():
    """record_collective must push duration_ms into the per-op reservoir so a
    later summary_snapshot picks them up.
    """
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    _metrics.record_collective(op="allreduce", duration_ms=0.4, bytes_count=64)
    _metrics.record_collective(op="allreduce", duration_ms=0.6, bytes_count=64)
    summary = _metrics.summary_snapshot_and_reset()
    assert "allreduce" in summary
    assert summary["allreduce"]["n"] == 2


def test_summary_snapshot_computes_percentiles_from_known_samples():
    """Given 100 samples drawn from a known distribution, the percentiles
    should match the obvious values (nearest-rank).
    """
    fake = mock.Mock()
    _th.install_metrics_client(fake)
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
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    for _ in range(5):
        _metrics.record_collective(op="allreduce", duration_ms=0.3, bytes_count=1)
    for _ in range(7):
        _metrics.record_collective(op="grad_comm", duration_ms=0.5, bytes_count=1)
    summary = _metrics.summary_snapshot_and_reset()
    assert summary["allreduce"]["n"] == 5
    assert summary["grad_comm"]["n"] == 7


def test_summary_snapshot_resets_reservoirs():
    fake = mock.Mock()
    _th.install_metrics_client(fake)
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
    fake = mock.Mock()
    _th.install_metrics_client(fake)
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
    """Reservoir state must follow the same gating as counters/distributions —
    pre-bootstrap samples are dropped so they can't pollute the first
    post-bootstrap rank-span summary.
    """
    _th.reset_device_cache()
    _metrics.record_collective(op="allreduce", duration_ms=0.5, bytes_count=1)
    assert _metrics.summary_snapshot_and_reset() == {}


def test_ticker_emits_nonzero_rates_correlated_with_recorded_events():
    """The ticker's rate distributions should reflect actual counter values
    accumulated by record_collective, not stale zeros.
    """
    fake = mock.Mock()
    _th.install_metrics_client(fake)
    ticker = _metrics.RateTicker(interval_s=0.05)
    ticker.start()
    try:
        # Drive 10 distinct collectives with known byte counts.
        for _ in range(10):
            _metrics.record_collective(op="allreduce", duration_ms=0.5, bytes_count=1024)
        time.sleep(0.12)  # let the ticker fire at least once
    finally:
        ticker.stop()

    rate_samples = [
        c
        for c in fake.distribution.call_args_list
        if c.args[0] in ("collective.calls_per_sec", "collective.bytes_per_sec")
    ]
    assert rate_samples, "ticker emitted no rate metrics"
    # At least one calls_per_sec sample should be non-zero (we recorded 10 collectives).
    calls_values = [c.args[1] for c in rate_samples if c.args[0] == "collective.calls_per_sec"]
    assert any(v > 0 for v in calls_values), "calls_per_sec was always zero"
    bytes_values = [c.args[1] for c in rate_samples if c.args[0] == "collective.bytes_per_sec"]
    assert any(v > 0 for v in bytes_values), "bytes_per_sec was always zero"


def test_rate_ticker_uses_configured_interval(monkeypatch):
    from ddtrace import config

    monkeypatch.setattr(config.pytorch, "rate_ticker_interval_s", 0.25, raising=False)

    t = _metrics.RateTicker(interval_s=float(config.pytorch.rate_ticker_interval_s))
    assert abs(t._interval_s - 0.25) < 1e-9
