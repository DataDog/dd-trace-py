import math
import re
from typing import Optional

from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.constants import SAMPLING_KNUTH_FACTOR


_MAX_THRESHOLD = 1 << 56
_MAX_ENCODABLE_THRESHOLD = _MAX_THRESHOLD - 1
_MAX_OTEL_TRACESTATE_VALUE_CHARS = 256
_VALID_RANDOM_VALUE = re.compile(r"^[0-9a-f]{14}$")
_VALID_THRESHOLD = re.compile(r"^[0-9a-f]{1,14}$")


def _parse_otel_fields(ot_value: Optional[str]) -> tuple[Optional[str], Optional[str], list[str]]:
    random_value = None
    threshold = None
    unknown_fields: list[str] = []

    if ot_value is None:
        return random_value, threshold, unknown_fields

    for item in ot_value.split(";"):
        if not item:
            continue
        key, separator, value = item.partition(":")
        if key == "rv":
            random_value = value if separator else None
        elif key == "th":
            threshold = value if separator else None
        else:
            unknown_fields.append(item)

    if random_value is not None and _VALID_RANDOM_VALUE.fullmatch(random_value) is None:
        random_value = None
    if threshold is not None and _VALID_THRESHOLD.fullmatch(threshold) is None:
        threshold = None

    return random_value, threshold, unknown_fields


def _random_value(trace_id: int) -> int:
    hashed_trace_id = ((trace_id & MAX_UINT_64BITS) * SAMPLING_KNUTH_FACTOR) & MAX_UINT_64BITS
    return (~hashed_trace_id & MAX_UINT_64BITS) >> 8


def _threshold(sample_rate: float) -> int:
    if sample_rate == 1.0:
        return 0
    # Python's round uses ties-to-even. OTEP 235 requires ties away from zero.
    threshold = math.floor((1.0 - sample_rate) * _MAX_THRESHOLD + 0.5)
    return min(_MAX_ENCODABLE_THRESHOLD, max(0, threshold))


def _format_threshold(threshold: int) -> str:
    return "{:014x}".format(threshold).rstrip("0") or "0"


def _build_otel_member(random_value: Optional[str], threshold: Optional[str], unknown_fields: list[str]) -> str:
    candidate_fields: list[str] = []
    if random_value is not None:
        candidate_fields.append("rv:{}".format(random_value))
    if threshold is not None:
        candidate_fields.append("th:{}".format(threshold))
    candidate_fields.extend(unknown_fields)

    # The ot value (excluding the "ot=" key) is limited to 256 characters. Keep
    # complete sub-fields, prioritizing rv/th, rather than truncating an invalid value.
    fields: list[str] = []
    value_chars = 0
    for field in candidate_fields:
        field_chars = len(field) + (1 if fields else 0)
        if value_chars + field_chars <= _MAX_OTEL_TRACESTATE_VALUE_CHARS:
            fields.append(field)
            value_chars += field_chars
    return ";".join(fields)


def normalize_otel_member(ot_value: str) -> str:
    """Validate and canonicalize an inherited ot= list-member value."""
    return _build_otel_member(*_parse_otel_fields(ot_value))


def resolve_otel_sampling_decision(
    ot_value: Optional[str],
    trace_id: Optional[int],
    sampled: bool,
    sample_rate: float,
    probabilistic_decision: bool,
) -> str:
    """Resolve the canonical ot= value after a local sampling decision.

    Valid inherited sampling fields remain authoritative because the tracer follows
    the upstream sampled bit. Explicit non-probabilistic decisions erase th while
    preserving a valid inherited rv and unknown fields.
    """
    # AIDEV-NOTE: Valid inherited sampling fields remain authoritative because the
    # tracer follows the upstream sampled bit. Only a non-probabilistic decision
    # invalidates an inherited threshold.
    if ot_value is None:
        if not probabilistic_decision or sample_rate <= 0.0 or trace_id is None:
            return ""

        threshold_value = _threshold(sample_rate)
        random_value_int = _random_value(trace_id)
        if sampled and random_value_int < threshold_value:
            random_value_int = threshold_value
        elif not sampled and random_value_int >= threshold_value:
            random_value_int = max(0, threshold_value - 1)
        if threshold_value == 0:
            return "rv:{:014x};th:0".format(random_value_int)
        return "rv:{:014x};th:{}".format(random_value_int, _format_threshold(threshold_value))

    random_value, threshold, unknown_fields = _parse_otel_fields(ot_value)

    # A zero probability cannot be represented by OTel's 56-bit rejection threshold.
    if not probabilistic_decision or sample_rate <= 0.0:
        return _build_otel_member(random_value, None, unknown_fields)

    if random_value is not None or threshold is not None:
        return _build_otel_member(random_value, threshold, unknown_fields)

    if trace_id is None:
        return _build_otel_member(random_value, threshold, unknown_fields)

    threshold_value = _threshold(sample_rate)
    random_value_int = _random_value(trace_id)
    if sampled and random_value_int < threshold_value:
        random_value_int = threshold_value
    elif not sampled and random_value_int >= threshold_value:
        random_value_int = max(0, threshold_value - 1)

    return _build_otel_member(
        "{:014x}".format(random_value_int),
        _format_threshold(threshold_value),
        unknown_fields,
    )
