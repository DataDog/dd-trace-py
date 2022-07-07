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

if TYPE_CHECKING:
    from typing import Dict
    from typing import Text

    from ddtrace.context import Context

    from ..span import Span

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
        "service_matcher",
        "name_matcher",
        "sample_rate",
        "max_per_second",
        "sampling_id_threshold",
        "limiter",
        "matcher",
    )

    def __init__(
        self,
        service=None,  # type: Optional[str]
        name=None,  # type: Optional[str]
        sample_rate=1.0,  # type: Optional[float]
        max_per_second=None,  # type: Optional[int]
    ):
        self.set_sample_rate(sample_rate)
        self.max_per_second = max_per_second
        # If no max_per_second specified then there is no limit
        if max_per_second is None:
            self.limiter = RateLimiter(-1)
        else:
            self.limiter = RateLimiter(max_per_second)

        # we need to create matchers for the service and/or name pattern provided
        if service:
            self.service_matcher = GlobMatcher(service)
        if name:
            self.name_matcher = GlobMatcher(name)

    def sample(self, span):
        # type: (Span) -> bool
        if self.match(span) and self._sample(span):
            if self.limiter.is_allowed(span.start_ns):
                self.apply_span_sampling_tags(span)
                return True
        return False

    def _sample(self, span):
        # type: (Span) -> bool
        if self.sample_rate == 1:
            return True
        elif self.sample_rate == 0:
            return False

        return ((span.span_id * KNUTH_FACTOR) % MAX_SPAN_ID) <= self.sampling_id_threshold

    def match(self, span):
        """Determines if the span's service and name match the configured patterns
        We check if a service and/or name pattern were given, and if so evaluate them against the span.
        """
        if hasattr(self, "service_matcher") and hasattr(self, "name_matcher"):
            return self.service_matcher.match(span.service) and self.name_matcher.match(span.name)
        elif hasattr(self, "service_matcher"):
            return self.service_matcher.match(span.service)
        elif hasattr(self, "name_matcher"):
            return self.name_matcher.match(span.name)
        else:
            return False

    def set_sample_rate(self, sample_rate=1.0):
        self.sample_rate = float(sample_rate)
        self.sampling_id_threshold = self.sample_rate * MAX_SPAN_ID

    def apply_span_sampling_tags(self, span):
        span.set_metric(_SINGLE_SPAN_SAMPLING_MECHANISM, SamplingMechanism.SPAN_SAMPLING_RULE)
        span.set_metric(_SINGLE_SPAN_SAMPLING_RATE, self.sample_rate)
        if self.max_per_second:
            span.set_metric(_SINGLE_SPAN_SAMPLING_MAX_PER_SEC, self.max_per_second)
