from typing import Any
from typing import Optional
from typing import Tuple

from ddtrace._trace.span import Span
from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.constants import SAMPLING_HASH_MODULO
from ddtrace.internal.constants import SAMPLING_KNUTH_FACTOR
from ddtrace.internal.glob_matching import GlobMatcher
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import cachedmethod


log = get_logger(__name__)


class SamplingRule(object):
    """
    Definition of a sampling rule used by :class:`DatadogSampler` for applying a sample rate on a span
    """

    NO_RULE = object()

    def __init__(
        self,
        sample_rate: float,
        service: Any = NO_RULE,
        name: Any = NO_RULE,
        resource: Any = NO_RULE,
        tags: Any = NO_RULE,
        provenance: str = "default",
    ) -> None:
        """
        Configure a new :class:`SamplingRule`

        .. code:: python

            DatadogSampler([
                # Sample 100% of any trace
                SamplingRule(sample_rate=1.0),

                # Sample no healthcheck traces
                SamplingRule(sample_rate=0, name='flask.request'),

                # Sample all services ending in `-db` based on a regular expression
                SamplingRule(sample_rate=0.5, service=re.compile('-db$')),

                # Sample based on service name using custom function
                SamplingRule(sample_rate=0.75, service=lambda service: 'my-app' in service),
            ])

        :param sample_rate: The sample rate to apply to any matching spans
        :type sample_rate: :obj:`float` clamped between 0.0 and 1.0 inclusive
        :param service: Rule to match the `span.service` on, default no rule defined
        :type service: :obj:`object` to directly compare, :obj:`function` to evaluate, or :class:`re.Pattern` to match
        :param name: Rule to match the `span.name` on, default no rule defined
        :type name: :obj:`object` to directly compare, :obj:`function` to evaluate, or :class:`re.Pattern` to match
        :param tags: A dictionary whose keys exactly match the names of tags expected to appear on spans, and whose
            values are glob-matches with the expected span tag values. Glob matching supports "*" meaning any
            number of characters, and "?" meaning any one character. If all tags specified in a SamplingRule are
            matches with a given span, that span is considered to have matching tags with the rule.
        """
        self.sample_rate = min(1.0, max(0.0, float(sample_rate)))
        # since span.py converts None to 'None' for tags, and does not accept 'None' for metrics
        # we can just create a GlobMatcher for 'None' and it will match properly
        self._tag_value_matchers = (
            {k: GlobMatcher(str(v)) for k, v in tags.items()} if tags != SamplingRule.NO_RULE else {}
        )
        self.tags = tags
        self.service = self._choose_matcher(service)
        self.name = self._choose_matcher(name)
        self.resource = self._choose_matcher(resource)
        self.provenance = provenance

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, sample_rate: float) -> None:
        self._sample_rate = sample_rate
        self._sampling_id_threshold = sample_rate * MAX_UINT_64BITS

    def _pattern_matches(self, prop, pattern):
        # If the rule is not set, then assume it matches
        # DEV: Having no rule and being `None` are different things
        #   e.g. ignoring `span.service` vs `span.service == None`
        if pattern is self.NO_RULE:
            return True
        if isinstance(pattern, GlobMatcher):
            return pattern.match(str(prop))
        # Exact match on the values
        return prop == pattern

    @cachedmethod()
    def _matches(self, key: Tuple[Optional[str], str, Optional[str]]) -> bool:
        # self._matches exists to maintain legacy pattern values such as regex and functions
        service, name, resource = key
        for prop, pattern in [(service, self.service), (name, self.name), (resource, self.resource)]:
            if not self._pattern_matches(prop, pattern):
                return False
        else:
            return True

    def matches(self, span: Span) -> bool:
        """
        Return if this span matches this rule

        :param span: The span to match against
        :type span: :class:`ddtrace._trace.span.Span`
        :returns: Whether this span matches or not
        :rtype: :obj:`bool`
        """
        return self.tags_match(span) and self._matches((span.service, span.name, span.resource))

    def tags_match(self, span: Span) -> bool:
        if not self._tag_value_matchers:
            return True

        meta = span._meta or {}
        metrics = span._metrics or {}
        if not meta and not metrics:
            return False

        for tag_key, pattern in self._tag_value_matchers.items():
            value = meta.get(tag_key, metrics.get(tag_key))
            if value is None:
                # If the tag is not present, we failed the match
                # (Metrics and meta do not support the value None)
                return False

            if isinstance(value, float):
                # Floats: Convert floats that represent integers to int for matching. This is because
                # SamplingRules only support integers for matfching or glob patterns.
                if value.is_integer():
                    value = int(value)
                elif set(pattern.pattern) - {"?", "*"}:
                    # Only match floats to patterns that only contain wildcards (ex: * or ?*)
                    # This is because we do not want to match floats to patterns like `23.*`.
                    return False

            if not pattern.match(str(value)):
                return False

        return True

    def sample(self, span):
        """
        Return if this rule chooses to sample the span

        :param span: The span to sample against
        :type span: :class:`ddtrace._trace.span.Span`
        :returns: Whether this span was sampled
        :rtype: :obj:`bool`
        """
        if self.sample_rate == 1:
            return True
        elif self.sample_rate == 0:
            return False

        return ((span._trace_id_64bits * SAMPLING_KNUTH_FACTOR) % SAMPLING_HASH_MODULO) <= self._sampling_id_threshold

    def _no_rule_or_self(self, val):
        if val is self.NO_RULE:
            return "NO_RULE"
        elif val is None:
            return "None"
        elif type(val) == GlobMatcher:
            return val.pattern
        else:
            return val

    def _choose_matcher(self, prop):
        if prop is SamplingRule.NO_RULE:
            return SamplingRule.NO_RULE
        elif prop is None:
            # Name and Resource will never be None, but service can be, since we str()
            #  whatever we pass into the GlobMatcher, we can just use its matching
            return GlobMatcher("None")
        return GlobMatcher(prop)

    def __repr__(self):
        return "{}(sample_rate={!r}, service={!r}, name={!r}, resource={!r}, tags={!r}, provenance={!r})".format(
            self.__class__.__name__,
            self.sample_rate,
            self._no_rule_or_self(self.service),
            self._no_rule_or_self(self.name),
            self._no_rule_or_self(self.resource),
            self._no_rule_or_self(self.tags),
            self.provenance,
        )

    __str__ = __repr__

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, SamplingRule):
            return False
        return str(self) == str(other)
