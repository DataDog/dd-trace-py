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
        self.sample_rate = float(min(1, max(0, sample_rate)))
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
    def _matches(self, key: Tuple[Optional[str], str, Optional[str]]) -> bool:
        # self._matches exists to maintain legacy pattern values such as regex and functions
        service, name, resource = key
        # perf: If a pattern is not matched we can skip the rest of the checks and return False
        return all(
            True if pattern is None else pattern.match(str(prop))
            for prop, pattern in ((service, self.service), (name, self.name), (resource, self.resource))
        )

    def matches(self, span: Span) -> bool:
        """
        Return if this span matches this rule

        :param span: The span to match against
        :type span: :class:`ddtrace._trace.span.Span`
        :returns: Whether this span matches or not
        :rtype: :obj:`bool`
        """
        tags_match = self.tags_match(span)
        return tags_match and self._matches((span.service, span.name, span.resource))

    def tags_match(self, span: Span) -> bool:
        tag_match = True
        if self.tags:
            tag_match = self.check_tags(span.get_tags(), span.get_metrics())
        return tag_match

    def check_tags(self, meta, metrics):
        if meta is None and metrics is None:
            return False

        tag_match = False
        for tag_key in self.tags.keys():
            value = meta.get(tag_key)
            # it's because we're not checking metrics first before continuing
            if value is None:
                value = metrics.get(tag_key)
                if value is None:
                    continue
                # Floats: Matching floating point values with a non-zero decimal part is not supported.
                # For floating point values with a non-zero decimal part, any all * pattern always returns true.
                # Other patterns always return false.
                if isinstance(value, float):
                    if not value.is_integer():
                        if all(c == "*" for c in self.tags[tag_key].pattern):
                            tag_match = True
                            continue
                        else:
                            return False
                    else:
                        value = int(value)

            tag_match = self.tags[tag_key].match(str(value))
            # if we don't match with all specified tags for a rule, it's not a match
            if tag_match is False:
                return False

        return tag_match

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
        return "{}(sample_rate={!r}, service={!r}, name={!r}, resource={!r}, tags={!r}, provenance={!r})".format(
            self.__class__.__name__,
            self.sample_rate,
            self.service,
            self.name,
            self.resource,
            {k: v.pattern for k, v in self.tags.items()},
            self.provenance,
        )

    __str__ = __repr__

    def __eq__(self, other: Any) -> bool:
        if not isinstance(other, SamplingRule):
            return False
        return str(self) == str(other)
