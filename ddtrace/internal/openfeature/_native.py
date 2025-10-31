"""
Native interface for FFE (Feature Flagging and Experimentation) processing.

This module provides the interface to the PyO3 native function that processes
feature flag configuration rules.
"""

import json
from dataclasses import dataclass
from typing import Optional, Any, Dict

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature._config import _set_ffe_config

from ddtrace.internal.native._native import ffe


log = get_logger(__name__)

VariationType = ffe.FlagType


@dataclass
class AssignmentValue:
    """Wrapper for flag values."""

    variation_type: VariationType
    value: Any


@dataclass
class Assignment:
    """Assignment result from flag evaluation."""

    value: AssignmentValue
    variation_key: str
    allocation_key: str
    reason: Any  # Native ffe.Reason or fallback string
    do_log: bool
    extra_logging: Dict[str, str]


class EvaluationError(Exception):
    """Error raised during flag evaluation."""

    def __init__(self, kind: str, *, expected: Optional[VariationType] = None, found: Optional[VariationType] = None):
        super().__init__(kind)
        self.kind = kind
        self.expected = expected
        self.found = found


def _infer_variation_type(value: Any):
    """Infer VariationType/FlagType from a Python value."""
    if isinstance(value, bool):
        return VariationType.Boolean
    elif isinstance(value, int):
        return VariationType.Integer
    elif isinstance(value, float):
        return VariationType.Float
    elif isinstance(value, str):
        return VariationType.String
    else:
        return VariationType.Object


def process_ffe_configuration(config):
    """
    Process FFE configuration and store as native Configuration object.

    Converts a dict config to JSON bytes and creates a native Configuration.

    Args:
        config: Configuration dict in format {"flags": {...}} or wrapped format
    """
    # Check if config needs to be wrapped in the UniversalFlagConfig format
    # The native FFE expects: {"flags": {...}}
    config_json = json.dumps(config)
    config_bytes = config_json.encode("utf-8")
    try:
        native_config = ffe.Configuration(config_bytes)
        print("native_config!!!!!!!!!!!!!!!!!!!!!!")
        print(native_config)
        _set_ffe_config(native_config)
    except ValueError as e:
        log.debug(
            "Failed to parse FFE configuration. The native library expects complete server format with: "
            "key, enabled, variationType, defaultVariation, variations (with type), and allocations fields. "
            "Error: %s", e, exc_info=True
        )
        raise


def get_assignment(
    configuration,
    flag_key: str,
    subject: Any,
    expected_type: Optional[VariationType],
    now: Any,
) -> Optional[Assignment]:
    """
    Thin wrapper around native resolve_value that converts types.

    Args:
        configuration: Native ffe.Configuration object
        flag_key: The flag key to evaluate
        subject: The evaluation context
        expected_type: Expected variation type
        now: Current datetime (ignored, native uses system time)

    Returns:
        Assignment object or None if flag not found/disabled

    Raises:
        EvaluationError: On type mismatch or other evaluation errors
    """
    if configuration is None:
        return None

    # Convert evaluation context to dict
    context_dict = {}
    if subject is not None:
        if hasattr(subject, "targeting_key"):
            context_dict["targetingKey"] = subject.targeting_key
        if hasattr(subject, "attributes") and subject.attributes:
            context_dict.update(subject.attributes)

    # Direct native call: config.resolve_value(flag_key, FlagType, context_dict)
    details = configuration.resolve_value(flag_key, expected_type, context_dict)

    # Handle errors from native
    if details.error_code is not None:
        if details.error_code == ffe.ErrorCode.TypeMismatch:
            found_type = _infer_variation_type(details.value) if details.value is not None else None
            raise EvaluationError("TYPE_MISMATCH", expected=expected_type, found=found_type)
        elif details.error_code == ffe.ErrorCode.FlagNotFound:
            return None
        else:
            raise EvaluationError(details.error_message or "Unknown error")

    # Return None if no value
    if details.value is None:
        return None

    # Convert native ResolutionDetails to Assignment
    return Assignment(
        value=AssignmentValue(variation_type=_infer_variation_type(details.value), value=details.value),
        variation_key=details.variant or "default",
        allocation_key=details.variant or "default",
        reason=details.reason,  # Pass native ffe.Reason directly
        do_log=details.do_log,
        extra_logging=details.extra_logging or {},
    )
