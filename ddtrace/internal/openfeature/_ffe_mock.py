from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
import json
from typing import Any
from typing import Dict
from typing import Optional

from ddtrace.internal.openfeature._config import _set_ffe_config


class VariationType(Enum):
    STRING = "STRING"
    INTEGER = "INTEGER"
    NUMERIC = "NUMERIC"
    BOOLEAN = "BOOLEAN"
    JSON = "JSON"


class AssignmentReason(Enum):
    TARGETING_MATCH = "TARGETING_MATCH"
    SPLIT = "SPLIT"
    STATIC = "STATIC"


@dataclass
class AssignmentValue:
    variation_type: VariationType
    value: Any


@dataclass
class Assignment:
    value: AssignmentValue
    variation_key: str
    allocation_key: str
    reason: AssignmentReason
    do_log: bool
    extra_logging: Dict[str, str]


@dataclass
class EvaluationContext:
    targeting_key: str
    attributes: Dict[str, Any]


class EvaluationError(Exception):
    def __init__(self, kind: str, *, expected: Optional[VariationType] = None, found: Optional[VariationType] = None):
        super().__init__(kind)
        self.kind = kind
        self.expected = expected
        self.found = found


def mock_process_ffe_configuration(config):
    config_json = json.dumps(config, ensure_ascii=False)
    _set_ffe_config(config_json)


def mock_get_assignment(
    configuration: Optional[Dict[str, Any]],
    flag_key: str,
    subject: Any,
    expected_type: Optional[VariationType],
    now: datetime,
) -> Optional[Assignment]:
    """
    Emulates Rust get_assignment:
      - Returns None when configuration missing or flag not found/disabled (non-error failures).
      - Raises EvaluationError on type mismatch (error failures).
      - Returns Assignment on success.

    configuration schema (minimal):
      {
        "flags": {
          "<flag_key>": {
            "enabled": bool,
            "variation_type": VariationType,
            "value": Any,
            "variation_key": str,          # optional; default "default"
            "allocation_key": str,         # optional; default "default"
            "reason": AssignmentReason,     # optional; default STATIC
            "do_log": bool,                # optional; default False
            "extra_logging": Dict[str,str] # optional; default {}
          }
        }
      }
    """
    if configuration is None:
        return None

    flags = configuration.get("flags", {})
    flag = flags.get(flag_key)
    if not flag or not flag.get("enabled", True):
        return None

    variation_type_raw = flag["variationType"]
    if isinstance(variation_type_raw, str):
        found_type = VariationType(variation_type_raw)
    else:
        found_type = variation_type_raw

    if expected_type is not None and expected_type != found_type:
        raise EvaluationError(
            "TYPE_MISMATCH",
            expected=expected_type,
            found=found_type,
        )

    reason_raw = flag.get("reason", AssignmentReason.STATIC)
    if isinstance(reason_raw, str):
        reason = AssignmentReason(reason_raw)
    else:
        reason = reason_raw

    value = list(flag["variations"].values())[0]["value"]
    assignment_value = AssignmentValue(variation_type=found_type, value=value)
    return Assignment(
        value=assignment_value,
        variation_key=flag.get("variation_key", "default"),
        allocation_key=flag.get("allocation_key", "default"),
        reason=reason,
        do_log=flag.get("do_log", False),
        extra_logging=flag.get("extra_logging", {}),
    )
