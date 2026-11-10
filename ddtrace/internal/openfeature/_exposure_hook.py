from types import MappingProxyType
import typing

from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.hook import HookHints

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._exposure_decision import METADATA_EXPOSURE_CACHE_HIT
from ddtrace.internal.openfeature._exposure_decision import complete_exposure_decision
from ddtrace.internal.openfeature._flageval_metrics import METADATA_ALLOCATION_KEY
from ddtrace.internal.openfeature._span_enrichment import METADATA_DO_LOG
from ddtrace.internal.openfeature._split_features import METADATA_FEATURE_PREFIX
from ddtrace.internal.openfeature._split_features import features_with_prefix


log = get_logger(__name__)


class ExposureEvaluation:
    """A completed flag evaluation and its Datadog exposure decision."""

    __slots__ = ("_details", "_evaluation_context")

    def __init__(
        self, details: FlagEvaluationDetails[typing.Any], evaluation_context: typing.Optional[EvaluationContext]
    ) -> None:
        self._details = details
        self._evaluation_context = evaluation_context

    @property
    def details(self) -> FlagEvaluationDetails[typing.Any]:
        """The flag key, value, variant, error and flag metadata of the evaluation."""
        return self._details

    @property
    def flag_key(self) -> str:
        return self._details.flag_key

    @property
    def variant(self) -> typing.Optional[str]:
        """The selected variant key, or None when the default value was returned."""
        return self._details.variant

    @property
    def allocation_key(self) -> typing.Optional[str]:
        """The key of the selected allocation, or None when none was selected."""
        allocation_key = self._metadata.get(METADATA_ALLOCATION_KEY)
        return allocation_key if isinstance(allocation_key, str) else None

    @property
    def evaluation_context(self) -> typing.Optional[EvaluationContext]:
        """The evaluation context, including the targeting key."""
        return self._evaluation_context

    @property
    def targeting_key(self) -> typing.Optional[str]:
        return self._evaluation_context.targeting_key if self._evaluation_context is not None else None

    @property
    def is_exposure(self) -> bool:
        """True when the evaluation succeeded and selected an allocation that logs exposures,
        whether or not this subject was already exposed.
        """
        return (
            self._details.error_code is None
            and self._details.variant is not None
            and self.allocation_key is not None
            and self._metadata.get(METADATA_DO_LOG) is True
        )

    @property
    def is_cache_hit(self) -> bool:
        """True when this exposure repeats one already recorded for this subject: same
        allocation, variant and serial id. False for a new or changed exposure and for an
        evaluation that is not an exposure.
        """
        return self.is_exposure and self._metadata.get(METADATA_EXPOSURE_CACHE_HIT) is True

    @property
    def should_send(self) -> bool:
        """True for a new or changed exposure: the decision the provider uses for Datadog."""
        return self.is_exposure and self._metadata.get(METADATA_EXPOSURE_CACHE_HIT) is False

    @property
    def features(self) -> typing.Mapping[str, typing.Union[str, float, bool]]:
        """The read-only HOOK features of the selected split, such as holdout.key.

        Values are a str, float or bool. Empty when the split has none.
        """
        return MappingProxyType(features_with_prefix(self._metadata, METADATA_FEATURE_PREFIX))

    @property
    def _metadata(self) -> typing.Mapping[str, typing.Any]:
        metadata = self._details.flag_metadata
        return metadata if metadata is not None else {}


class ExposureHook(Hook):
    """An OpenFeature hook that hands each completed flag evaluation to a callback, with the
    Datadog exposure decision, so an application can record exposures in its own data store.

    Register it with openfeature.api.add_hooks([ExposureHook(callback)]) or on a client.
    The callback receives an ExposureEvaluation for every evaluation, including errors and
    repeats. Use should_send to keep the new or changed exposures: these are exactly the
    exposures the provider sends to Datadog. With
    DD_FEATURE_FLAGS_EXPOSURES_DATADOG_LOGGING_ENABLED=false, Datadog receives no exposures
    and should_send gives the same results.

    The callback runs on the evaluation thread: hand the data to a background queue instead
    of doing I/O in the callback. An exception from the callback does not affect the
    evaluation. The first one is logged at warning level, later ones at debug level.
    """

    def __init__(self, callback: typing.Callable[[ExposureEvaluation], None]) -> None:
        if not callable(callback):
            raise TypeError("ExposureHook requires a callable callback")
        self._callback = callback
        self._warned = False

    def finally_after(
        self,
        hook_context: HookContext,
        details: FlagEvaluationDetails[typing.Any],
        hints: HookHints,
    ) -> None:
        try:
            # The provider's own hook normally completes the decision first; this covers
            # wrappers that skip provider hooks, so the callback still reads a final verdict.
            complete_exposure_decision(details)
            evaluation_context = getattr(details.flag_metadata, "_dd_evaluation_context", None)
            if evaluation_context is None:
                evaluation_context = hook_context.evaluation_context
            self._callback(ExposureEvaluation(details, evaluation_context))
        except Exception:
            try:
                if self._warned:
                    log.debug("Exposure callback failed", exc_info=True)
                else:
                    self._warned = True
                    log.warning("Exposure callback failed; later failures are logged at debug level", exc_info=True)
            except Exception:  # nosec B110
                pass
