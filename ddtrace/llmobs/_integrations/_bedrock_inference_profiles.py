"""Process-local cache mapping AWS Bedrock application-inference-profile ARNs to
their underlying base model ids.

Populated two ways, both consulted by the botocore integration when it processes a
Bedrock API call whose `modelId` is an application-inference-profile ARN:
- the langchain integration, when it sees a ChatBedrockConverse instance carrying
  both `model_id` (the profile ARN) and `base_model_id` (the underlying model); and
- the botocore integration itself, via an opt-in `bedrock:GetInferenceProfile` call.

A profile's underlying model is fixed at creation, so a resolved mapping is cached for
the life of the process. A failed resolution is not cached; instead the ARN enters a
non-blocking exponential backoff so retries are throttled (no per-request storm during an
outage) but never stop, so a healed outage or a fixed IAM permission recovers on its own
without a restart. A single-flight claim prevents concurrent callers from all issuing the
resolution call at once.
"""

from contextlib import contextmanager
import random
import threading
import time
from typing import Optional


_INFERENCE_PROFILE_CACHE: dict[str, str] = {}
# profile ARN -> [next-allowed monotonic time, consecutive failure count]
_RESOLVE_BACKOFF: dict[str, list] = {}
# profile ARNs a thread is currently attempting to resolve (single-flight)
_RESOLVE_IN_FLIGHT: set[str] = set()
_CACHE_LOCK = threading.Lock()

# Exponential backoff schedule for failed resolutions (mirrors the 1.618 factor used by
# ddtrace.internal.utils.retry.fibonacci_backoff_with_jitter): ~1s, ~1.6s, ~2.6s, ... capped.
_BACKOFF_BASE_SECONDS = 1.0
_BACKOFF_FACTOR = 1.618
_BACKOFF_MAX_SECONDS = 600.0
# Bound the failure map so a long-lived process hitting many distinct invalid ARNs can't
# grow it without limit.
_BACKOFF_MAX_ENTRIES = 512


def record_inference_profile(profile_arn: str, base_model_id: str) -> None:
    if not profile_arn or not base_model_id:
        return
    with _CACHE_LOCK:
        _INFERENCE_PROFILE_CACHE[profile_arn] = base_model_id
        _RESOLVE_BACKOFF.pop(profile_arn, None)


def lookup_inference_profile(profile_arn: str) -> Optional[str]:
    if not profile_arn:
        return None
    with _CACHE_LOCK:
        return _INFERENCE_PROFILE_CACHE.get(profile_arn)


def try_begin_resolve(profile_arn: str) -> bool:
    """Atomically claim the right to attempt resolving `profile_arn` now. Returns False if
    another thread is already attempting it or it is still within its failure backoff window.
    A caller that receives True MUST call ``end_resolve`` when done (use try/finally).
    """
    if not profile_arn:
        return False
    with _CACHE_LOCK:
        if profile_arn in _RESOLVE_IN_FLIGHT:
            return False
        if _within_backoff_locked(profile_arn):
            return False
        _RESOLVE_IN_FLIGHT.add(profile_arn)
        return True


def end_resolve(profile_arn: str) -> None:
    if not profile_arn:
        return
    with _CACHE_LOCK:
        _RESOLVE_IN_FLIGHT.discard(profile_arn)


@contextmanager
def begin_resolve(profile_arn: str):
    """Yield True if the caller claimed the single-flight slot for `profile_arn` (not already
    in flight and past any backoff window), else False. Releases the claim on exit.
    """
    claimed = try_begin_resolve(profile_arn)
    try:
        yield claimed
    finally:
        if claimed:
            end_resolve(profile_arn)


def record_resolve_failure(profile_arn: str) -> tuple[float, int]:
    """Widen the backoff for `profile_arn` after a failed resolution and return
    ``(delay_seconds, failure_count)``. The interval grows per consecutive failure up to
    ``_BACKOFF_MAX_SECONDS`` (with jitter) but never stops, so resolution recovers on its own.
    """
    if not profile_arn:
        return 0.0, 0
    now = time.monotonic()
    with _CACHE_LOCK:
        if profile_arn not in _RESOLVE_BACKOFF and len(_RESOLVE_BACKOFF) >= _BACKOFF_MAX_ENTRIES:
            _evict_backoff_locked(now)
        count = _RESOLVE_BACKOFF.get(profile_arn, [0.0, 0])[1] + 1
        capped = min(_BACKOFF_BASE_SECONDS * (_BACKOFF_FACTOR ** (count - 1)), _BACKOFF_MAX_SECONDS)
        # Half-jitter: keep at least half the interval (so it still spaces out) while
        # de-synchronizing retries across processes.
        delay = capped * random.uniform(0.5, 1.0)  # nosec B311 - not security-sensitive
        _RESOLVE_BACKOFF[profile_arn] = [now + delay, count]
        return delay, count


def is_resolve_in_backoff(profile_arn: str) -> bool:
    """True if `profile_arn` failed recently and its backoff window has not yet elapsed."""
    if not profile_arn:
        return False
    with _CACHE_LOCK:
        return _within_backoff_locked(profile_arn)


def _within_backoff_locked(profile_arn: str) -> bool:
    # True if the ARN's backoff window has not yet elapsed. Caller holds _CACHE_LOCK.
    entry = _RESOLVE_BACKOFF.get(profile_arn)
    return entry is not None and entry[0] > time.monotonic()


def _evict_backoff_locked(now: float) -> None:
    # Drop entries whose backoff window has elapsed (they would be retried anyway); if still
    # at capacity, drop the soonest-to-expire entry to make room. Caller holds _CACHE_LOCK.
    for arn in [arn for arn, entry in _RESOLVE_BACKOFF.items() if entry[0] <= now]:
        _RESOLVE_BACKOFF.pop(arn, None)
    if len(_RESOLVE_BACKOFF) >= _BACKOFF_MAX_ENTRIES:
        soonest = min(_RESOLVE_BACKOFF, key=lambda arn: _RESOLVE_BACKOFF[arn][0])
        _RESOLVE_BACKOFF.pop(soonest, None)


def _clear_inference_profile_cache() -> None:
    with _CACHE_LOCK:
        _INFERENCE_PROFILE_CACHE.clear()
        _RESOLVE_BACKOFF.clear()
        _RESOLVE_IN_FLIGHT.clear()
