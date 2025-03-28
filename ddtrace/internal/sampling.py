import json
import re
from typing import TYPE_CHECKING  # noqa:F401
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Text


# TypedDict was added to typing in python 3.8
try:
    from typing import TypedDict  # noqa:F401
except ImportError:
    from typing_extensions import TypedDict

from ddtrace._trace.sampling_rule import SamplingRule  # noqa:F401
from ddtrace.constants import _SAMPLING_AGENT_DECISION
from ddtrace.constants import _SAMPLING_RULE_DECISION
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MAX_PER_SEC
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MAX_PER_SEC_NO_LIMIT
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MECHANISM
from ddtrace.constants import _SINGLE_SPAN_SAMPLING_RATE
from ddtrace.internal.constants import _KEEP_PRIORITY_INDEX
from ddtrace.internal.constants import _REJECT_PRIORITY_INDEX
from ddtrace.internal.constants import MAX_UINT_64BITS as _MAX_UINT_64BITS
from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY
from ddtrace.internal.constants import SAMPLING_HASH_MODULO as _SAMPLING_HASH_MODULO
from ddtrace.internal.constants import SAMPLING_KNUTH_FACTOR as _SAMPLING_KNUTH_FACTOR
from ddtrace.internal.constants import SAMPLING_MECHANISM_TO_PRIORITIES
from ddtrace.internal.constants import SamplingMechanism
from ddtrace.internal.glob_matching import GlobMatcher
from ddtrace.internal.logger import get_logger
from ddtrace.settings._config import config

from .rate_limiter import RateLimiter


log = get_logger(__name__)

try:
    from json.decoder import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

if TYPE_CHECKING:  # pragma: no cover
    from ddtrace._trace.context import Context  # noqa:F401
    from ddtrace._trace.span import Span  # noqa:F401


class PriorityCategory(object):
    DEFAULT = "default"
    AUTO = "auto"
    RULE_DEFAULT = "rule_default"
    RULE_CUSTOMER = "rule_customer"
    RULE_DYNAMIC = "rule_dynamic"


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


def validate_sampling_decision(
    meta: Dict[str, str],
) -> Dict[str, str]:
    value = meta.get(SAMPLING_DECISION_TRACE_TAG_KEY)
    if value:
        # Skip propagating invalid sampling mechanism trace tag
        if TRACE_TAG_RE.match(value) is None:
            del meta[SAMPLING_DECISION_TRACE_TAG_KEY]
            meta["_dd.propagation_error"] = "decoding_error"
            log.warning("failed to decode _dd.p.dm: %r", value)
    return meta


def set_sampling_decision_maker(
    context,  # type: Context
    sampling_mechanism: int,
) -> Optional[Text]:
    value = "-%d" % sampling_mechanism
    context._meta[SAMPLING_DECISION_TRACE_TAG_KEY] = value
    return value


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
        sample_rate: float,
        max_per_second: int,
        service: Optional[str] = None,
        name: Optional[str] = None,
    ):
        self._sample_rate = sample_rate
        self._sampling_id_threshold = self._sample_rate * _MAX_UINT_64BITS

        self._max_per_second = max_per_second
        self._limiter = RateLimiter(max_per_second)

        # we need to create matchers for the service and/or name pattern provided
        self._service_matcher = GlobMatcher(service) if service is not None else None
        self._name_matcher = GlobMatcher(name) if name is not None else None

    def sample(self, span):
        # type: (Span) -> bool
        if self._sample(span):
            if self._limiter.is_allowed():
                self.apply_span_sampling_tags(span)
                return True
        return False

    def _sample(self, span):
        # type: (Span) -> bool
        if self._sample_rate == 1:
            return True
        elif self._sample_rate == 0:
            return False

        return ((span.span_id * _SAMPLING_KNUTH_FACTOR) % _SAMPLING_HASH_MODULO) <= self._sampling_id_threshold

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


def get_span_sampling_rules() -> List[SpanSamplingRule]:
    json_rules = _get_span_sampling_json()
    sampling_rules = []
    for rule in json_rules:
        # If sample_rate not specified default to 100%
        sample_rate = rule.get("sample_rate", 1.0)
        service = rule.get("service")
        name = rule.get("name")

        if not service and not name:
            log.warning("Sampling rules must supply at least 'service' or 'name', got %s", json.dumps(rule))
            return []

        # If max_per_second not specified default to no limit
        max_per_second = rule.get("max_per_second", _SINGLE_SPAN_SAMPLING_MAX_PER_SEC_NO_LIMIT)

        try:
            if service:
                _check_unsupported_pattern(service)
            if name:
                _check_unsupported_pattern(name)
            sampling_rule = SpanSamplingRule(
                sample_rate=sample_rate, service=service, name=name, max_per_second=max_per_second
            )
        except Exception as e:
            log.warning("Error creating single span sampling rule %s: %s", json.dumps(rule), e)
        else:
            sampling_rules.append(sampling_rule)
    return sampling_rules


def _get_span_sampling_json() -> List[Dict[str, Any]]:
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


def _get_file_json() -> Optional[List[Dict[str, Any]]]:
    file_json_raw = config._sampling_rules_file
    if file_json_raw:
        with open(file_json_raw) as f:
            return _load_span_sampling_json(f.read())
    return None


def _get_env_json() -> Optional[List[Dict[str, Any]]]:
    env_json_raw = config._sampling_rules
    if env_json_raw:
        return _load_span_sampling_json(env_json_raw)
    return None


def _load_span_sampling_json(raw_json_rules: str) -> List[Dict[str, Any]]:
    try:
        json_rules = json.loads(raw_json_rules)
        if not isinstance(json_rules, list):
            log.warning("DD_SPAN_SAMPLING_RULES is not list, got %r", json_rules)
            return []
    except JSONDecodeError:
        log.warning("Unable to parse DD_SPAN_SAMPLING_RULES=%r", raw_json_rules)
        return []

    return json_rules


def _check_unsupported_pattern(string: str) -> None:
    # We don't support pattern bracket expansion or escape character
    unsupported_chars = {"[", "]", "\\"}
    for char in string:
        if char in unsupported_chars and config._raise:
            raise ValueError("Unsupported Glob pattern found, character:%r is not supported" % char)


def is_single_span_sampled(span):
    # type: (Span) -> bool
    return span.get_metric(_SINGLE_SPAN_SAMPLING_MECHANISM) == SamplingMechanism.SPAN_SAMPLING_RULE


def _set_sampling_tags(span, sampled, sample_rate, mechanism):
    # type: (Span, bool, float, int) -> None
    # Set the sampling mechanism
    set_sampling_decision_maker(span.context, mechanism)
    # Set the sampling psr rate
    if mechanism in (
        SamplingMechanism.LOCAL_USER_TRACE_SAMPLING_RULE,
        SamplingMechanism.REMOTE_USER_TRACE_SAMPLING_RULE,
        SamplingMechanism.REMOTE_DYNAMIC_TRACE_SAMPLING_RULE,
    ):
        span.set_metric(_SAMPLING_RULE_DECISION, sample_rate)
    elif mechanism == SamplingMechanism.AGENT_RATE_BY_SERVICE:
        span.set_metric(_SAMPLING_AGENT_DECISION, sample_rate)
    # Set the sampling priority
    priorities = SAMPLING_MECHANISM_TO_PRIORITIES[mechanism]
    priority_index = _KEEP_PRIORITY_INDEX if sampled else _REJECT_PRIORITY_INDEX
    span.context.sampling_priority = priorities[priority_index]


def _get_highest_precedence_rule_matching(span, rules):
    # type: (Span, List[SamplingRule]) -> Optional[SamplingRule]
    if not rules:
        return None

    for rule in rules:
        if rule.matches(span):
            return rule
    return None
