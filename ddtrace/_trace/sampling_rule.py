from typing import Any
from typing import Dict
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

    def __init__(
        self,
        sample_rate: float,
        service: Optional[str] = None,
        name: Optional[str] = None,
        resource: Optional[str] = None,
        tags: Optional[Dict[str, Any]] = None,
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
    def name_match(self, key: Tuple[Optional[str], str, Optional[str]]) -> bool:
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

        meta = span._meta or {}
        metrics = span._metrics or {}
        if not meta and not metrics:
            return False

        for tag_key, pattern in self.tags.items():
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
