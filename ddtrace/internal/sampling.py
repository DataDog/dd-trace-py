import json
import os
import re
from typing import Optional
from typing import TYPE_CHECKING


# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict
except ImportError:
    from typing_extensions import TypedDict

from jsonschema import validate

from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MAX_PER_SEC
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MAX_PER_SEC_NO_LIMIT
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_RATE
from ddtrace.internal.glob_matching import GlobMatcher
from ddtrace.internal.logger import get_logger

from .rate_limiter import RateLimiter


log = get_logger(__name__)

try:
    from json.decoder import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

if TYPE_CHECKING:
    from typing import Any
    from typing import Dict
    from typing import List
    from typing import Text

    from ddtrace.context import Context
    from ddtrace.span import Span

# Big prime number to make hashing better distributed
KNUTH_FACTOR = 1111111111111111111
MAX_SPAN_ID = 2 ** 64


class SamplingMechanism(object):
    DEFAULT = 0
    AGENT_RATE = 1
    REMOTE_RATE = 2
    TRACE_SAMPLING_RULE = 3
    MANUAL = 4
    APPSEC = 5
    REMOTE_RATE_USER = 6
    REMOTE_RATE_DATADOG = 7
    SPAN_SAMPLING_RULE = 8


SAMPLING_DECISION_TRACE_TAG_KEY = "_dd.p.dm"

# Use regex to validate trace tag value
TRACE_TAG_RE = re.compile(r"^-([0-9])$")


SpanSamplingRules = TypedDict(
    "SpanSamplingRules",
    {
        "name": str,
        "service": str,
        "sample_rate": float,
        "max_per_second": int,
    },
    total=False,
)

SPAN_SAMPLING_JSON_SCHEMA = {
    "type": "array",
    "items": {
        "type": "object",
        "anyOf": [
            {"properties": {"service": {"type": "string"}}, "required": ["service"]},
            {"properties": {"name": {"type": "string"}}, "required": ["name"]},
        ],
        "properties": {"max_per_second": {"type": "integer"}, "sample_rate": {"type": "number"}},
    },
}


def _set_trace_tag(
    context,  # type: Context
    sampling_mechanism,  # type: int
):
    # type: (...) -> Optional[Text]

    value = "-%d" % sampling_mechanism

    context._meta[SAMPLING_DECISION_TRACE_TAG_KEY] = value

    return value


def _unset_trace_tag(
    context,  # type: Context
):
    # type: (...) -> Optional[Text]
    if SAMPLING_DECISION_TRACE_TAG_KEY not in context._meta:
        return None

    value = context._meta[SAMPLING_DECISION_TRACE_TAG_KEY]
    del context._meta[SAMPLING_DECISION_TRACE_TAG_KEY]
    return value


def validate_sampling_decision(
    meta,  # type: Dict[str, str]
):
    # type: (...) -> Dict[str, str]
    value = meta.get(SAMPLING_DECISION_TRACE_TAG_KEY)
    if value:
        # Skip propagating invalid sampling mechanism trace tag
        if TRACE_TAG_RE.match(value) is None:
            del meta[SAMPLING_DECISION_TRACE_TAG_KEY]
            meta["_dd.propagation_error"] = "decoding_error"
            log.warning("failed to decode _dd.p.dm: %r", value, exc_info=True)
    return meta


def update_sampling_decision(
    context,  # type: Context
    sampling_mechanism,  # type: int
    sampled,  # type: bool
):
    # type: (...) -> Optional[Text]
    # When sampler keeps trace, we need to set sampling decision trace tag.
    # If sampler rejects trace, we need to remove sampling decision trace tag to avoid unnecessary propagation.
    if sampled:
        return _set_trace_tag(context, sampling_mechanism)
    else:
        return _unset_trace_tag(context)


class SpanSamplingRule:
    """A span sampling rule to evaluate and potentially tag each span upon finish."""

    __slots__ = (
        "_service_matcher",
        "_name_matcher",
        "_sample_rate",
        "_max_per_second",
        "_sampling_id_threshold",
        "_limiter",
        "_matcher",
    )

    def __init__(
        self,
        sample_rate,  # type: float
        max_per_second,  # type: int
        service=None,  # type: Optional[str]
        name=None,  # type: Optional[str]
    ):
        self._sample_rate = sample_rate
        self._sampling_id_threshold = self._sample_rate * MAX_SPAN_ID

        self._max_per_second = max_per_second
        self._limiter = RateLimiter(max_per_second)

        # we need to create matchers for the service and/or name pattern provided
        self._service_matcher = GlobMatcher(service) if service is not None else None
        self._name_matcher = GlobMatcher(name) if name is not None else None

    def sample(self, span):
        # type: (Span) -> bool
        if self._sample(span):
            if self._limiter.is_allowed(span.start_ns):
                self.apply_span_sampling_tags(span)
                return True
        return False

    def _sample(self, span):
        # type: (Span) -> bool
        if self._sample_rate == 1:
            return True
        elif self._sample_rate == 0:
            return False

        return ((span.span_id * KNUTH_FACTOR) % MAX_SPAN_ID) <= self._sampling_id_threshold

    def match(self, span):
        # type: (Span) -> bool
        """Determines if the span's service and name match the configured patterns"""
        name = span.name
        service = span.service
        # If a span lacks a name and service, we can't match on it
        if service is None and name is None:
            return False

        # Default to True, as the rule may not have a name or service rule
        # For whichever rules it does have, it will attempt to match on them
        service_match = True
        name_match = True

        if self._service_matcher:
            if service is None:
                return False
            else:
                service_match = self._service_matcher.match(service)
        if self._name_matcher:
            if name is None:
                return False
            else:
                name_match = self._name_matcher.match(name)
        return service_match and name_match

    def apply_span_sampling_tags(self, span):
        # type: (Span) -> None
        span.set_metric(_SINGLE_SPAN_SAMPLING_MECHANISM, SamplingMechanism.SPAN_SAMPLING_RULE)
        span.set_metric(_SINGLE_SPAN_SAMPLING_RATE, self._sample_rate)
        # Only set this tag if it's not the default -1
        if self._max_per_second != _SINGLE_SPAN_SAMPLING_MAX_PER_SEC_NO_LIMIT:
            span.set_metric(_SINGLE_SPAN_SAMPLING_MAX_PER_SEC, self._max_per_second)


def get_span_sampling_rules():
    # type: () -> List[SpanSamplingRule]
    json_rules = _get_span_sampling_json()
    validate(json_rules, SPAN_SAMPLING_JSON_SCHEMA)
    sampling_rules = []
    for rule in json_rules:
        # If sample_rate not specified default to 100%
        sample_rate = rule.get("sample_rate", 1.0)
        service = rule.get("service")
        name = rule.get("name")
        # If max_per_second not specified default to no limit
        max_per_second = rule.get("max_per_second", _SINGLE_SPAN_SAMPLING_MAX_PER_SEC_NO_LIMIT)
        if service:
            _check_unsupported_pattern(service)
        if name:
            _check_unsupported_pattern(name)

        try:
            sampling_rule = SpanSamplingRule(
                sample_rate=sample_rate, service=service, name=name, max_per_second=max_per_second
            )
        except Exception as e:
            raise ValueError("Error creating single span sampling rule {}: {}".format(json.dumps(rule), e))
        sampling_rules.append(sampling_rule)
    return sampling_rules


def _get_span_sampling_json():
    # type: () -> List[Dict[str, Any]]
    env_json_rules = _get_env_json()
    file_json_rules = _get_file_json()

    if env_json_rules and file_json_rules:
        log.warning(
            (
                "DD_SPAN_SAMPLING_RULES and DD_SPAN_SAMPLING_RULES_FILE detected. "
                "Defaulting to DD_SPAN_SAMPLING_RULES value."
            )
        )
        return env_json_rules
    return env_json_rules or file_json_rules or []


def _get_file_json():
    # type: () -> Optional[List[Dict[str, Any]]]
    file_json_raw = os.getenv("DD_SPAN_SAMPLING_RULES_FILE")
    if file_json_raw:
        with open(file_json_raw) as f:
            return _load_span_sampling_json(f.read())
    return None


def _get_env_json():
    # type: () -> Optional[List[Dict[str, Any]]]
    env_json_raw = os.getenv("DD_SPAN_SAMPLING_RULES")
    if env_json_raw:
        return _load_span_sampling_json(env_json_raw)
    return None


def _load_span_sampling_json(raw_json_rules):
    # type: (str) -> List[Dict[str, Any]]
    try:
        json_rules = json.loads(raw_json_rules)
        if not isinstance(json_rules, list):
            raise TypeError("DD_SPAN_SAMPLING_RULES is not list, got %r" % json_rules)
    except JSONDecodeError:
        raise ValueError("Unable to parse DD_SPAN_SAMPLING_RULES=%r" % raw_json_rules)

    return json_rules


def _check_unsupported_pattern(string):
    # type: (str) -> None
    # We don't support pattern bracket expansion or escape character
    unsupported_chars = {"[", "]", "\\"}
    for char in string:
        if char in unsupported_chars:
            raise ValueError("Unsupported Glob pattern found, character:%r is not supported" % char)


def is_single_span_sampled(span):
    # type: (Span) -> bool
    return span.get_metric(_SINGLE_SPAN_SAMPLING_MECHANISM) == SamplingMechanism.SPAN_SAMPLING_RULE
