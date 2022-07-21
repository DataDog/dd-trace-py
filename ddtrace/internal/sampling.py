import json
import os
import re
from typing import Optional
from typing import TYPE_CHECKING

from ddtrace.constants import _SINGLE_SPAN_SAMPLING_MAX_PER_SEC
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
    from typing import Dict
    from typing import List
    from typing import Text

    from ddtrace.context import Context

    from ..span import Span

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
        service=None,  # type: Optional[str]
        name=None,  # type: Optional[str]
        sample_rate=1.0,  # type: Optional[float]
        max_per_second=None,  # type: Optional[int]
    ):
        self.set_sample_rate(sample_rate)
        self._max_per_second = max_per_second
        # If no max_per_second specified then there is no limit
        if max_per_second is None:
            self._limiter = RateLimiter(-1)
        else:
            self._limiter = RateLimiter(max_per_second)

        # we need to create matchers for the service and/or name pattern provided
        if service is None:
            self._service_matcher = GlobMatcher("*")
        else:
            self._service_matcher = GlobMatcher(service)

        if name is None:
            self._name_matcher = GlobMatcher("*")
        else:
            self._name_matcher = GlobMatcher(name)

    def sample(self, span):
        # type: (Span) -> bool
        if self.match(span) and self._sample(span):
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
        """Determines if the span's service and name match the configured patterns"""
        return self._service_matcher.match(span.service) and self._name_matcher.match(span.name)

    def set_sample_rate(self, sample_rate=1.0):
        self._sample_rate = float(sample_rate)
        self._sampling_id_threshold = self._sample_rate * MAX_SPAN_ID

    def apply_span_sampling_tags(self, span):
        span.set_metric(_SINGLE_SPAN_SAMPLING_MECHANISM, SamplingMechanism.SPAN_SAMPLING_RULE)
        span.set_metric(_SINGLE_SPAN_SAMPLING_RATE, self._sample_rate)
        if self._max_per_second:
            span.set_metric(_SINGLE_SPAN_SAMPLING_MAX_PER_SEC, self._max_per_second)


class SpanSamplingRules:
    """To generate a list of SpanSamplingRule objects and pass them to the SpanProcessor"""

    def __init__(self):
        self._rules = self._make_rules()

    def get_single_span_sampling_rules(self):
        # type: () -> List[SpanSamplingRule]
        return self._rules

    def _make_rules(self):
        # type: () -> List[SpanSamplingRule]
        json_rules = os.getenv("DD_SPAN_SAMPLING_RULES")
        if not json_rules:
            return []
        else:
            return self._parse_rules(json_rules)

    def _parse_rules(self, rules):
        sampling_rules = []
        if rules is not None:
            json_rules = []
            try:
                json_rules = json.loads(rules)
            except JSONDecodeError:
                raise ValueError("Unable to parse DD_SPAN_SAMPLING_RULES={}".format(rules))
            for rule in json_rules:
                sample_rate = rule.get("sample_rate", 1.0)
                service = rule.get("service")
                name = rule.get("name")
                max_per_second = rule.get("max_per_second")
                if service is None and name is None:
                    raise SingleSpanSamplingError("Neither service or name specified for single span sampling rule")
                if (service and self._unsupported_pattern(service)) or (name and self._unsupported_pattern(name)):
                    raise UnsupportedGlobPatternError

                try:
                    sampling_rule = SpanSamplingRule(
                        sample_rate=sample_rate, service=service, name=name, max_per_second=max_per_second
                    )
                except ValueError as e:
                    raise ValueError("Error creating single span sampling rule {}: {}".format(json.dumps(rule), e))
                sampling_rules.append(sampling_rule)
        return sampling_rules

    def _unsupported_pattern(self, string):
        # We don't support pattern bracket expansion or escape character
        unsupported_chars = ["[", "]", "\\"]
        for char in string:
            if char in unsupported_chars:
                return True
        return False


class SingleSpanSamplingError(Exception):
    """Raised when neither a name or service are specified for a single span sampling rule"""


class UnsupportedGlobPatternError(Exception):
    """Raised when either a name or service pattern uses glob matching that we do not support.
    Only special characters "?" and "*" are supported.
    """
