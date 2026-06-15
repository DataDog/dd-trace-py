"""Layer Zero metric reservoir for the PyTorch contrib integration.

Per-op duration samples feed the ``collective.<op>.{p10,p50,p90,p99,n}_ms``
facets emitted on the rotated ``pytorch.rank`` span via
``summary_snapshot_and_reset()``. DogStatsD emission has been removed;
all data reaches users as span facets only.

AIDEV-NOTE: The reservoir is the sole surviving Layer Zero data path. The
former DogStatsD client, RateTicker thread, and counter dict were removed in
the "pytorch-remove-layer-zero-dogstatsd" change. Keep this note so future
readers understand why there is no DogStatsD client here.
"""

import os
import random
import threading

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def record_collective(op: str, duration_ms: float, bytes_count: int) -> None:
    """Push a collective observation into the per-op duration reservoir.

    ``bytes_count`` is accepted for source compatibility with callers in
    ``_distributed.py`` but is no longer recorded; DogStatsD emission was
    removed. Only ``duration_ms`` is stored (in the reservoir that feeds
    the ``pytorch.rank`` span facets via ``summary_snapshot_and_reset()``).

    AIDEV-NOTE: Gated on device discovery; pre-bootstrap collectives are
    dropped to avoid mis-attribution to the post-discovery device tag.
    """
    from ddtrace.contrib.internal.pytorch import _device  # noqa: PLC0415

    if _device.get() is None:
        return
    _push_duration(op, duration_ms)


# ---------------------------------------------------------------------------
# Per-op duration reservoirs for the pytorch.rank `collective.summary` tag
# ---------------------------------------------------------------------------

# AIDEV-NOTE: Reservoirs feed the per-window straggler summary attached to
# each rotated `pytorch.rank` span. Job-scoped tag (not a metric) on purpose:
# the investigator already filters by `training_job.id`, and tag storage on
# the span sidesteps metric cardinality concerns from job IDs.
_RESERVOIR_MAX = 1024

# _durations[op] = [observed_total, reservoir_list].
_counter_lock = threading.Lock()
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
    """Compute per-op p10/p50/p90/p99/n and clear the reservoir state.

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


# AIDEV-NOTE: After fork() only the caller's thread survives. Reset the
# reservoir globals (lock, durations) so the child can bootstrap fresh
# without inheriting a held lock.
def _reset_child_state() -> None:
    global _counter_lock, _durations
    _counter_lock = threading.Lock()
    _durations = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_child_state)
