"""Routing of the selected split's features into flag metadata."""

import typing

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

FEATURE_DESTINATION_HOOK = "HOOK"
FEATURE_DESTINATION_EXPOSURE = "EXPOSURE"
FEATURE_DESTINATION_EVALUATION = "EVALUATION"

# Only the HOOK prefix is a public contract; the other two feed the SDK's own
# exposure and evaluation-count paths.
METADATA_FEATURE_PREFIX = "__dd_feature."
METADATA_EXPOSURE_FEATURE_PREFIX = "__dd_exposure_feature."
METADATA_EVALUATION_FEATURE_PREFIX = "__dd_evaluation_feature."
METADATA_HAS_EVALUATION_FEATURES = "__dd_has_evaluation_features"

_PREFIXES = {
    FEATURE_DESTINATION_HOOK: METADATA_FEATURE_PREFIX,
    FEATURE_DESTINATION_EXPOSURE: METADATA_EXPOSURE_FEATURE_PREFIX,
    FEATURE_DESTINATION_EVALUATION: METADATA_EVALUATION_FEATURE_PREFIX,
}

# Bounds the warned set so a configuration cannot grow it without limit.
MAX_WARNED_DESTINATIONS = 64
_warned_destinations: set[str] = set()

FeatureValue = typing.Union[str, float, bool]
NativeFeatures = typing.Optional[typing.Sequence[tuple[str, FeatureValue, typing.Sequence[str]]]]


def add_split_features(metadata: dict[str, typing.Any], features: NativeFeatures) -> None:
    """Write one metadata entry per (feature, known destination).

    The native parser already drops invalid entries and keeps the first feature of a
    repeated key, so every feature here is valid and has a unique key.
    """
    if not features:
        return
    has_evaluation_features = False
    for key, value, destinations in features:
        for destination in destinations:
            prefix = _PREFIXES.get(destination)
            if prefix is None:
                _warn_unknown_destination(destination)
                continue
            metadata[prefix + key] = value
            if prefix is METADATA_EVALUATION_FEATURE_PREFIX:
                has_evaluation_features = True
    if has_evaluation_features:
        metadata[METADATA_HAS_EVALUATION_FEATURES] = True


def _warn_unknown_destination(destination: str) -> None:
    if destination in _warned_destinations or len(_warned_destinations) >= MAX_WARNED_DESTINATIONS:
        return
    _warned_destinations.add(destination)
    log.warning("Ignoring unknown feature destination %s", destination)


def features_with_prefix(
    metadata: typing.Optional[typing.Mapping[str, typing.Any]], prefix: str
) -> dict[str, typing.Any]:
    """Return the features stored under prefix, keyed without it."""
    if not metadata:
        return {}
    return {key[len(prefix) :]: value for key, value in metadata.items() if key.startswith(prefix)}
