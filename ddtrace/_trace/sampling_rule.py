from typing import Any
from typing import Optional

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

    def __init__(
        self,
        sample_rate: float,
        service: Optional[str] = None,
        name: Optional[str] = None,
        resource: Optional[str] = None,
        tags: Optional[dict[str, Any]] = None,
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
        self.tags = {k: GlobMatcher(str(v)) for k, v in tags.items()} if tags else {}
        self.service = GlobMatcher(service) if service is not None else None
        self.name = GlobMatcher(name) if name is not None else None
        self.resource = GlobMatcher(resource) if resource else None
        self.provenance = provenance

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, sample_rate: float) -> None:
        self._sample_rate = sample_rate
        self._sampling_id_threshold = sample_rate * MAX_UINT_64BITS

    @cachedmethod()
    def name_match(self, key: tuple[Optional[str], str, Optional[str]]) -> bool:
        service, name, resource = key
        for prop, pattern in ((service, self.service), (name, self.name), (resource, self.resource)):
            # perf: If a pattern is not matched we can skip the rest of the checks and return False
            if pattern is not None and not pattern.match(str(prop)):
                return False
        return True

    def matches(self, span: Span) -> bool:
        """
        Return if this span matches this rule

        :param span: The span to match against
        :type span: :class:`ddtrace._trace.span.Span`
        :returns: Whether this span matches or not
        :rtype: :obj:`bool`
        """
        return self.tags_match(span) and self.name_match((span.service, span.name, span.resource))

    def tags_match(self, span: Span) -> bool:
        if not self.tags:
            return True

        for tag_key, pattern in self.tags.items():
            # Check first for an explicit str value
            value = span._get_str_attribute(tag_key)

            # Otherwise, check for a numeric value
            if value is None:
                num_val = span._get_numeric_attribute(tag_key)
                if num_val is None:
                    # If the tag is not present as a str or float value, we failed the match
                    return False

                # SamplingRules only support integers for matching or glob patterns.
                if isinstance(num_val, int) or num_val.is_integer():
                    value = str(int(num_val))
                elif not pattern.wildcards_only:
                    # Only match floats to patterns that only contain wildcards (ex: * or ?*)
                    # This is because we do not want to match floats to patterns like `23.*`.
                    return False
                else:
                    # A float and a wildcards-only pattern
                    value = str(num_val)

            if not pattern.match(value):
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

    def __repr__(self):
        return (
            f"SamplingRule(sample_rate={self.sample_rate}, service={self.service}, "
            f"name={self.name}, resource={self.resource}, tags={self.tags}, provenance={self.provenance})"
        )

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, SamplingRule):
            return False
        return (
            self.sample_rate == other.sample_rate
            and self.service == other.service
            and self.name == other.name
            and self.resource == other.resource
            and self.tags == other.tags
            and self.provenance == other.provenance
        )
