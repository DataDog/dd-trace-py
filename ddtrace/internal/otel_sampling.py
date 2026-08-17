import math
import re
from typing import Optional

from ddtrace.internal.constants import DD_TRACE_TRACESTATE_MAX_BYTES
from ddtrace.internal.constants import DD_TRACE_TRACESTATE_MAX_ITEMS
from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.constants import SAMPLING_KNUTH_FACTOR


_MAX_THRESHOLD = 1 << 56
_MAX_ENCODABLE_THRESHOLD = _MAX_THRESHOLD - 1
_VALID_RANDOM_VALUE = re.compile(r"^[0-9a-f]{14}$")
_VALID_THRESHOLD = re.compile(r"^[0-9a-f]{1,14}$")


class OtelSamplingState:
    """Mutable trace-level state used to produce OTel sampling fields.

    A missing sample_rate means no local probabilistic decision was made. is_probabilistic
    defaults to true so inherited thresholds remain valid unless a local non-probabilistic
    decision explicitly invalidates them.
    """

    __slots__ = ("sample_rate", "is_probabilistic")

    def __init__(self, sample_rate: Optional[float] = None, is_probabilistic: bool = True) -> None:
        self.sample_rate = sample_rate
        self.is_probabilistic = is_probabilistic

    def set_probabilistic_decision(self, sample_rate: float) -> None:
        self.sample_rate = sample_rate
        self.is_probabilistic = True

    def set_non_probabilistic_decision(self) -> None:
        self.sample_rate = None
        self.is_probabilistic = False

    def clear(self) -> None:
        self.sample_rate = None
        self.is_probabilistic = True

    def is_default(self) -> bool:
        return self.sample_rate is None and self.is_probabilistic

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, OtelSamplingState)
            and self.sample_rate == other.sample_rate
            and self.is_probabilistic == other.is_probabilistic
        )

    def __reduce__(self) -> tuple[type["OtelSamplingState"], tuple[Optional[float], bool]]:
        return self.__class__, (self.sample_rate, self.is_probabilistic)


def _otel_sampling_states_equal(left: Optional[OtelSamplingState], right: Optional[OtelSamplingState]) -> bool:
    if left is None:
        return right is None or right.is_default()
    if right is None:
        return left.is_default()
    return left == right


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
    # Python's round uses ties-to-even. OTEP 235 requires ties away from zero.
    threshold = math.floor((1.0 - sample_rate) * _MAX_THRESHOLD + 0.5)
    return min(_MAX_ENCODABLE_THRESHOLD, max(0, threshold))


def _format_threshold(threshold: int) -> str:
    return "{:014x}".format(threshold).rstrip("0") or "0"


def _resolve_otel_fields(
    ot_value: Optional[str],
    trace_id: Optional[int],
    sampling_priority: Optional[float],
    otel_sampling_state: Optional[OtelSamplingState],
) -> tuple[Optional[str], Optional[str], list[str]]:
    # AIDEV-NOTE: Inbound rv/th always win over local derivation. A local rate is recorded only
    # when this tracer actually made the probability decision, so inherited sampled flags never
    # acquire fabricated OTel sampling fields here.
    random_value, threshold, unknown_fields = _parse_otel_fields(ot_value)

    if otel_sampling_state is not None and otel_sampling_state.is_probabilistic is False:
        return random_value, None, unknown_fields

    if random_value is not None or threshold is not None:
        return random_value, threshold, unknown_fields

    if (
        trace_id is None
        or sampling_priority is None
        or otel_sampling_state is None
        or otel_sampling_state.sample_rate is None
    ):
        return None, None, unknown_fields

    threshold_value = _threshold(otel_sampling_state.sample_rate)
    random_value_int = _random_value(trace_id)
    kept = sampling_priority > 0
    if kept and random_value_int < threshold_value:
        random_value_int = threshold_value
    elif not kept and random_value_int >= threshold_value:
        random_value_int = max(0, threshold_value - 1)

    return "{:014x}".format(random_value_int), _format_threshold(threshold_value), unknown_fields


def _build_otel_member(
    ot_value: Optional[str],
    trace_id: Optional[int],
    sampling_priority: Optional[float],
    otel_sampling_state: Optional[OtelSamplingState],
) -> str:
    random_value, threshold, unknown_fields = _resolve_otel_fields(
        ot_value, trace_id, sampling_priority, otel_sampling_state
    )
    fields = []
    if random_value is not None:
        fields.append("rv:{}".format(random_value))
    if threshold is not None:
        fields.append("th:{}".format(threshold))
    fields.extend(unknown_fields)
    return ";".join(fields)


def _limit_tracestate_members(leading_members: list[str], other_members: list[str]) -> str:
    members: list[str] = []
    total_bytes = 0

    for member in leading_members:
        member_bytes = len(member.encode("utf-8")) + (1 if members else 0)
        if members and total_bytes + member_bytes > DD_TRACE_TRACESTATE_MAX_BYTES:
            break
        members.append(member)
        total_bytes += member_bytes

    for member in other_members:
        if len(members) >= DD_TRACE_TRACESTATE_MAX_ITEMS:
            break
        member_bytes = len(member.encode("utf-8")) + (1 if members else 0)
        if total_bytes + member_bytes > DD_TRACE_TRACESTATE_MAX_BYTES:
            break
        members.append(member)
        total_bytes += member_bytes

    return ",".join(members)


def build_tracestate(
    raw_tracestate: str,
    dd_list_member: str,
    trace_id: Optional[int],
    sampling_priority: Optional[float],
    otel_sampling_state: Optional[OtelSamplingState],
) -> str:
    """Build tracestate with Datadog and OTel sampling members protected on the left."""
    if not raw_tracestate:
        ot_list_member = _build_otel_member(None, trace_id, sampling_priority, otel_sampling_state)
        if dd_list_member:
            dd_member = "dd={}".format(dd_list_member)
            if not ot_list_member:
                return dd_member
            combined = "{},ot={}".format(dd_member, ot_list_member)
            if len(combined.encode("utf-8")) <= DD_TRACE_TRACESTATE_MAX_BYTES:
                return combined
            return dd_member
        if ot_list_member:
            return "ot={}".format(ot_list_member)
        return ""

    raw_dd_list_member = None
    raw_ot_value = None
    other_members = []
    for raw_member in raw_tracestate.split(","):
        member = raw_member.strip()
        if member.startswith("dd="):
            raw_dd_list_member = member
        elif member.startswith("ot="):
            raw_ot_value = member[3:]
        elif member:
            other_members.append(member)

    ot_list_member = _build_otel_member(
        raw_ot_value,
        trace_id,
        sampling_priority,
        otel_sampling_state,
    )

    leading_members = []
    if dd_list_member:
        leading_members.append("dd={}".format(dd_list_member))
    elif raw_dd_list_member:
        leading_members.append(raw_dd_list_member)
    if ot_list_member:
        leading_members.append("ot={}".format(ot_list_member))

    return _limit_tracestate_members(leading_members, other_members)
