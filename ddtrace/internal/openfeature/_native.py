"""
Native interface for FFE (Feature Flagging and Experimentation) processing.

This module provides the interface to the PyO3 native function that processes
feature flag configuration rules.
"""

import json
import logging
from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import ffe
from ddtrace.internal.openfeature._config import _ConfigurationIdentity
from ddtrace.internal.openfeature._config import _FfeSnapshot
from ddtrace.internal.openfeature._config import _set_ffe_config


log = get_logger(__name__)

VariationType = ffe.FlagType
ResolutionDetails = ffe.ResolutionDetails
DEPENDENT_FLAG_VARIANT_KEY_V1 = "dependent-flag-variant-key-v1"


def get_evaluator_capabilities() -> tuple[str, ...]:
    """Return capabilities explicitly supported by the loaded native evaluator.

    Older native extensions have no reporter and therefore advertise no optional
    UFC features. Transport support alone never implies evaluator support.
    """
    reporter = getattr(ffe, "get_evaluator_capabilities", None)
    return tuple(reporter()) if reporter is not None else ()


def process_ffe_configuration(config: Any) -> bool:
    """
    Process FFE configuration and store as native Configuration object.

    Stores the parsed configuration together with the strict root-level
    observeFullEvaluationData consent value from the same UFC.

    Args:
        config: Configuration dict in format {"flags": {...}} or wrapped format

    Returns:
        True when the configuration was applied, False when the native library
        rejected it. On rejection the previously applied configuration is left
        in place, so callers that track delivery state (e.g. the agentless
        source's ETag) must not treat a False result as success.
    """
    try:
        raw_consent = config.get("observeFullEvaluationData") if isinstance(config, dict) else None
        observe_full_evaluation_data = raw_consent is True

        config_json = json.dumps(config)
        config_bytes = config_json.encode("utf-8")
        native_config = ffe.Configuration(config_bytes)
        identity: Optional[_ConfigurationIdentity] = None
        if log.isEnabledFor(logging.DEBUG):
            _log_flag_summary(config, native_config)
            if isinstance(config, dict):
                identity = _ConfigurationIdentity(
                    created_at=config.get("createdAt"), environment=config.get("environment")
                )
        # Notifies registered providers as part of setting the snapshot; see
        # ddtrace.internal.openfeature._config for why the registry lives there.
        _set_ffe_config(_FfeSnapshot(native_config, observe_full_evaluation_data, identity))
        return True
    except ValueError as e:
        log.debug(
            "Failed to parse FFE configuration. The native library expects complete server format with: "
            "key, enabled, variationType, defaultVariation, variations (with type), and allocations fields. "
            "Error: %s",
            e,
            exc_info=True,
        )
        return False


def _log_flag_summary(config: Any, native_config: ffe.Configuration) -> None:
    """Log how many flags of a parsed configuration are usable and which were rejected.

    The native parser keeps a configuration whose individual flags fail to parse and
    stores the failure in their place, so the configuration-level result alone hides
    them. The native API has no list of rejected flags, so each flag is looked up once
    to recover its status.
    """
    # This only reports on the configuration, so nothing in it may stop the
    # configuration from being applied.
    try:
        raw_flags = config.get("flags") if isinstance(config, dict) else None
        flags = raw_flags if isinstance(raw_flags, dict) else {}
        rejected: list[tuple[str, str]] = []
        for key in flags:
            # A rejected flag reports its parse error before the expected type is
            # considered, so the type passed here does not change the outcome.
            details = native_config.resolve_value(key, VariationType.Boolean, {"targeting_key": None, "attributes": {}})
            if details.error_code == ffe.ErrorCode.ParseError:
                rejected.append((key, details.error_message or "configuration error"))
                log.debug(
                    "Feature Flags: rejected flag %s: %s "
                    "(native evaluator does not expose field-level rejection details)",
                    key,
                    details.error_message,
                )
        log.debug(
            "Feature Flags: configuration parsed: %d flag(s), %d usable, %d rejected%s",
            len(flags),
            len(flags) - len(rejected),
            len(rejected),
            " (invalid flag configuration: %s)" % ", ".join(f"{key}: {reason}" for key, reason in sorted(rejected))
            if rejected
            else "",
        )
    except Exception:
        log.debug("Feature Flags: could not check parsed flags for rejections", exc_info=True)


def resolve_flag(
    configuration,
    flag_key: str,
    context: Any,
    expected_type: VariationType,
) -> Optional[ResolutionDetails]:
    """
    Wrapper around native resolve_value that prepares the context.

    Args:
        configuration: Native ffe.Configuration object
        flag_key: The flag key to evaluate
        context: The evaluation context
        expected_type: Expected variation type

    Returns:
        ResolutionDetails object or None if configuration is None
    """
    if configuration is None:
        return None

    # Convert evaluation context to dict for native FFE
    # The native library expects: {"targeting_key": "...", "attributes": {...}}
    context_dict: dict[str, Any] = {"targeting_key": None, "attributes": {}}

    if context is not None:
        # Handle dict input
        if isinstance(context, dict):
            # Try camelCase first (OpenFeature convention), then snake_case (native lib convention)
            targeting_key = context.get("targetingKey")
            if targeting_key is None:
                targeting_key = context.get("targeting_key")
            if targeting_key is not None:
                context_dict["targeting_key"] = targeting_key
            attributes = context.get("attributes", {})
            context_dict["attributes"] = attributes
        # Handle object with attributes
        elif hasattr(context, "targeting_key"):
            if context.targeting_key is not None:
                context_dict["targeting_key"] = context.targeting_key
            if hasattr(context, "attributes") and context.attributes:
                context_dict["attributes"] = context.attributes

    # Call native resolve_value which returns ResolutionDetails
    # ResolutionDetails contains: value, variant, reason, error_code, error_message,
    # allocation_key, do_log
    # JSON flags may contain "null" which is a valid value that should be returned.
    # The way to check for absent value is by checking variant field—if it's None,
    # then there's no value returned from evaluation.
    return configuration.resolve_value(flag_key, expected_type, context_dict)
