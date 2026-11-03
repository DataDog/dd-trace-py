"""One exposure-cache verdict per evaluation, shared by the Datadog exposure path and ExposureHook."""

import typing

from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagEvaluationDetails

from ddtrace.internal.openfeature.writer import ExposureEvent


METADATA_EXPOSURE_CACHE_HIT = "__dd_exposure_cache_hit"

ExposureCacheKey = tuple[str, str]
ExposureCacheValue = tuple[typing.Optional[str], typing.Optional[str], typing.Optional[int]]


class FlagMetadata(dict[str, typing.Any]):
    """Flag metadata that carries the pending exposure decision and the evaluated context.

    The OpenFeature SDK hands the provider's metadata object to every hook unchanged, so
    both travel with it without becoming metadata entries. The SDK does not write the
    context returned by before hooks back into the hook context, so hooks need this one.
    The slots are set only when used, so read them with getattr(..., None).
    """

    __slots__ = ("_dd_exposure_decision", "_dd_evaluation_context")
    _dd_exposure_decision: "typing.Optional[ExposureDecision]"
    _dd_evaluation_context: typing.Optional[EvaluationContext]

    def __reduce__(self) -> tuple[typing.Any, ...]:
        # Copies and pickles are plain dicts: the decision holds the cache and its lock.
        return (dict, (dict(self),))


class ExposureDecision:
    """Check and record an exposure once, after the evaluation has succeeded."""

    __slots__ = ("_cache", "_lock", "_key", "_value", "_completed", "event")

    def __init__(
        self,
        cache: typing.MutableMapping[ExposureCacheKey, ExposureCacheValue],
        lock: typing.ContextManager[typing.Any],
        key: ExposureCacheKey,
        value: ExposureCacheValue,
        event: ExposureEvent,
    ) -> None:
        self._cache = cache
        self._lock = lock
        self._key = key
        self._value = value
        self._completed = False
        self.event = event

    def is_cache_hit(self) -> bool:
        # Provisional, so no lock: complete() checks again under the lock.
        return self._cache.get(self._key) == self._value

    def complete(self, metadata: dict[str, typing.Any]) -> None:
        with self._lock:
            if self._completed:
                return
            hit = self._cache.get(self._key) == self._value
            if not hit:
                self._cache[self._key] = self._value
            self._completed = True
        metadata[METADATA_EXPOSURE_CACHE_HIT] = hit


def complete_exposure_decision(details: FlagEvaluationDetails[typing.Any]) -> typing.Optional[ExposureDecision]:
    """Finalize the decision of a successful evaluation; a failed one records nothing.

    Returns the decision when the details carry one, so the caller can read the event.
    """
    if details.error_code is not None:
        return None
    metadata = details.flag_metadata
    decision: typing.Optional[ExposureDecision] = getattr(metadata, "_dd_exposure_decision", None)
    if decision is None:
        return None
    decision.complete(typing.cast(dict[str, typing.Any], metadata))
    return decision
