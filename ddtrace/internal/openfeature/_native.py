"""
Native interface for FFE (Feature Flagging and Experimentation) processing.

This module provides the interface to the PyO3 native function that processes
feature flag configuration rules.
"""

import json
from typing import Any
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import ffe
from ddtrace.internal.openfeature._config import _set_ffe_config


log = get_logger(__name__)

VariationType = ffe.FlagType
ResolutionDetails = ffe.ResolutionDetails


def process_ffe_configuration(config):
    """
    Process FFE configuration and store as native Configuration object.

    Converts a dict config to JSON bytes and creates a native Configuration.

    Args:
        config: Configuration dict in format {"flags": {...}} or wrapped format
    """
    try:
        config_json = json.dumps(config)
        config_bytes = config_json.encode("utf-8")
        native_config = ffe.Configuration(config_bytes)
        _set_ffe_config(native_config)
    except ValueError as e:
        log.debug(
            "Failed to parse FFE configuration. The native library expects complete server format with: "
            "key, enabled, variationType, defaultVariation, variations (with type), and allocations fields. "
            "Error: %s",
            e,
            exc_info=True,
        )


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
    context_dict = {}
    if context is not None:
        if hasattr(context, "targeting_key") and context.targeting_key:
            context_dict["targetingKey"] = context.targeting_key
        if hasattr(context, "attributes") and context.attributes:
            context_dict.update(context.attributes)

    # Call native resolve_value which returns ResolutionDetails
    # ResolutionDetails contains: value, variant, reason, error_code, error_message,
    # allocation_key, do_log, extra_logging
    # JSON flags may contain "null" which is a valid value that should be returned.
    # The way to check for absent value is by checking variant field—if it's None,
    # then there's no value returned from evaluation.
    return configuration.resolve_value(flag_key, expected_type, context_dict)
