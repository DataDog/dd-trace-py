"""Layer Zero metric emission for the PyTorch contrib integration.

Per-event distributions (one DogStatsD sample per collective) carry full
spike resolution within the 10s flush interval — the agent locally
aggregates samples into a DDSketch so a single outlier among 250 normal
collectives still shows up in p99/max.

AIDEV-NOTE: Tags are intentionally device-scoped, not job-scoped. See
`_device.py` for the rationale.
"""

import os
import random
import threading
import time
from typing import Any
from typing import Optional

from ddtrace.contrib.internal.pytorch import _device
from ddtrace.internal.dogstatsd import get_dogstatsd_client
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings._agent import config as agent_config


log = get_logger(__name__)


_DOGSTATSD: Any = None  # vendored DogStatsd client; None when uninitialized.


def _ensure_client() -> Optional[Any]:
    global _DOGSTATSD
    if _DOGSTATSD is not None:
        return _DOGSTATSD
    try:
        _DOGSTATSD = get_dogstatsd_client(agent_config.dogstatsd_url, namespace="pytorch")
    except Exception:
        log.debug("pytorch: failed to initialize dogstatsd client; metrics disabled", exc_info=True)
        _DOGSTATSD = None
    return _DOGSTATSD


def _base_tags() -> Optional[list]:
    info = _device.get()
    if info is None:
        return None
    tags = ["device.id:%s" % info.device_id, "host:%s" % info.hostname, "kind:%s" % info.kind]
    if info.device_index is not None:
        tags.append("device.index:%d" % info.device_index)
    return tags


def record_collective(op: str, duration_ms: float, bytes_count: int) -> None:
    """Emit per-event distribution samples and update rate counters.

    AIDEV-NOTE: Gated on device discovery; pre-bootstrap collectives are
    dropped to avoid mis-attribution to the post-discovery device tag.
    """
    base = _base_tags()
    if base is None:
        return
    _bump_counters(op, bytes_count)
    _push_duration(op, duration_ms)
    client = _ensure_client()
    if client is None:
        return
    tags = base + ["op:%s" % op]
    try:
        client.distribution("collective.duration_ms", float(duration_ms), tags=tags)
        client.distribution("collective.bytes", float(bytes_count), tags=tags)
    except Exception:
        log.debug("pytorch: dogstatsd distribution send failed", exc_info=True)


# ---------------------------------------------------------------------------
# Rate counters and ticker
# ---------------------------------------------------------------------------

# Counters live in `_counters[(op,)] = [call_count, bytes_total]`. The
# hot-path increment runs under `_counter_lock`; acquisition is uncontested
# (only the ticker thread competes) and amortizes to ~50 ns on CPython.
_counter_lock = threading.Lock()
_counters: dict = {}


def _bump_counters(op: str, bytes_count: int) -> None:
    key = (op,)
    with _counter_lock:
        slot = _counters.get(key)
        if slot is None:
            _counters[key] = [1, int(bytes_count)]
        else:
            slot[0] += 1
            slot[1] += int(bytes_count)


def _snapshot_and_reset_counters() -> dict:
    """Atomically copy and clear `_counters`. Called from the ticker only."""
    with _counter_lock:
        snap = {k: (v[0], v[1]) for k, v in _counters.items()}
        _counters.clear()
    return snap


# ---------------------------------------------------------------------------
# Per-op duration reservoirs for the pytorch.rank `collective.summary` tag
# ---------------------------------------------------------------------------

# AIDEV-NOTE: Reservoirs feed the per-window straggler summary attached to
# each rotated `pytorch.rank` span. Job-scoped tag (not a metric) on purpose:
# the investigator already filters by `training_job.id`, and tag storage on
# the span sidesteps metric cardinality concerns from job IDs.
_RESERVOIR_MAX = 1024

# _durations[op] = [observed_total, reservoir_list].
# Shares `_counter_lock` — the hot path already takes it for counter bumps,
# so no extra synchronization cost beyond the per-call random draw.
_durations: dict = {}


def _push_duration(op: str, duration_ms: float) -> None:
    """Algorithm R reservoir sample of per-collective `duration_ms`.

    Past `_RESERVOIR_MAX` observations the buffer stops growing; each new
    sample replaces a uniformly-random existing one. This keeps percentile
    estimates unbiased over the entire window without unbounded memory.
    """
    v = float(duration_ms)
    with _counter_lock:
        slot = _durations.get(op)
        if slot is None:
            _durations[op] = [1, [v]]
            return
        slot[0] += 1
        buf = slot[1]
        if len(buf) < _RESERVOIR_MAX:
            buf.append(v)
            return
        # Reservoir is full: pick a victim index in [0, observed_total).
        j = random.randrange(slot[0])
        if j < _RESERVOIR_MAX:
            buf[j] = v


def _percentile(sorted_samples: list, p: float) -> float:
    """Nearest-rank percentile. `p` in [0, 1]. Caller passes a sorted list."""
    n = len(sorted_samples)
    idx = int(p * n)
    if idx >= n:
        idx = n - 1
    return sorted_samples[idx]


def summary_snapshot_and_reset() -> dict:
    """Compute per-op p10/p50/p90/n and clear the reservoir state.

    Called from `_rank_root` just before the rotated or closing
    `pytorch.rank` span is finished, so each span carries the summary
    for its own ~5-minute window.
    """
    with _counter_lock:
        snap = {op: (obs, list(buf)) for op, (obs, buf) in _durations.items()}
        _durations.clear()
    result: dict = {}
    for op, (observed, buf) in snap.items():
        if not buf:
            continue
        buf.sort()
        result[op] = {
            "p10": _percentile(buf, 0.10),
            "p50": _percentile(buf, 0.50),
            "p90": _percentile(buf, 0.90),
            "p99": _percentile(buf, 0.99),
            "n": observed,
        }
    return result


_FAILURE_BACKOFF_THRESHOLD = 10
_FAILURE_BACKOFF_DURATION_S = 60.0


class RateTicker:
    """Background thread that converts cumulative counters into rate-style
    distribution samples on a fast cadence (default 100 ms).

    AIDEV-NOTE: On `start()` we drain any counter state accumulated
    pre-start and seed `_last_tick_ns` so the first tick measures only
    post-start activity.

    AIDEV-NOTE: If `client.distribution` raises 10 times in a row the
    ticker disables itself for 60s. Without this, an unreachable
    dogstatsd agent would burn ~864k failed sends per day per rank.
    """

    def __init__(self, interval_s: float = 0.1) -> None:
        self._interval_s = interval_s
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._last_tick_ns: int = 0
        self._consecutive_failures: int = 0
        self._backoff_until_ns: int = 0

    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._last_tick_ns = time.perf_counter_ns()
        # Drain any counter state accumulated before start so the first
        # real tick measures only post-start activity.
        _snapshot_and_reset_counters()
        self._thread = threading.Thread(target=self._run, name="dd-pytorch-rate-ticker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 1.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)

    def _run(self) -> None:
        while not self._stop.wait(self._interval_s):
            self._emit_tick()

    def _emit_tick(self) -> None:
        now_ns = time.perf_counter_ns()
        dt_ns = max(now_ns - self._last_tick_ns, 1)
        self._last_tick_ns = now_ns
        # Backoff: drain counters and skip emission. Without the drain,
        # accumulated counts from the cool-down window would be divided by
        # the next post-recovery tick's small dt and produce a huge
        # `calls_per_sec` / `bytes_per_sec` spike on the agent's DDSketch.
        if self._backoff_until_ns > now_ns:
            _snapshot_and_reset_counters()
            return
        snap = _snapshot_and_reset_counters()
        if not snap:
            return
        base = _base_tags()
        if base is None:
            return
        client = _ensure_client()
        if client is None:
            return
        dt_s = dt_ns / 1e9
        tick_failed = False
        for (op,), (count, bytes_total) in snap.items():
            tags = base + ["op:%s" % op]
            try:
                client.distribution("collective.calls_per_sec", count / dt_s, tags=tags)
                client.distribution("collective.bytes_per_sec", bytes_total / dt_s, tags=tags)
            except Exception:
                tick_failed = True
                log.debug("pytorch: rate distribution send failed", exc_info=True)
        if tick_failed:
            self._consecutive_failures += 1
            if self._consecutive_failures >= _FAILURE_BACKOFF_THRESHOLD:
                self._backoff_until_ns = now_ns + int(_FAILURE_BACKOFF_DURATION_S * 1e9)
                log.warning(
                    "pytorch: rate ticker disabled for %.0fs after %d consecutive dogstatsd failures",
                    _FAILURE_BACKOFF_DURATION_S,
                    self._consecutive_failures,
                )
                # Reset so a still-broken agent can trip the threshold again
                # on the next post-backoff failure run.
                self._consecutive_failures = 0
        else:
            if self._consecutive_failures:
                log.info("pytorch: rate ticker recovered after %d failures", self._consecutive_failures)
            self._consecutive_failures = 0


# AIDEV-NOTE: After fork() only the caller's thread survives. Reset the
# Layer Zero globals (client, counters, lock) so the child can bootstrap
# fresh without inheriting a held lock or a stale ticker handle.
def _reset_child_state() -> None:
    global _DOGSTATSD, _counter_lock, _counters, _durations
    _DOGSTATSD = None
    _counter_lock = threading.Lock()
    _counters = {}
    _durations = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_child_state)
