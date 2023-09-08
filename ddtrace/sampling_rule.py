from typing import TYPE_CHECKING

from ddtrace.internal.compat import pattern_type
from ddtrace.internal.constants import MAX_UINT_64BITS as _MAX_UINT_64BITS
from ddtrace.internal.glob_matching import GlobMatcher
from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.cache import cachedmethod


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Optional
    from typing import Tuple

    from .span import Span

log = get_logger(__name__)
KNUTH_FACTOR = 1111111111111111111


class SamplingRule(object):
    """
    Definition of a sampling rule used by :class:`DatadogSampler` for applying a sample rate on a span
    """

    NO_RULE = object()

    def __init__(
        self,
        sample_rate,  # type: float
        service=NO_RULE,  # type: Any
        name=NO_RULE,  # type: Any
        resource=NO_RULE,  # type: Any
        tags=NO_RULE,  # type: Any
    ):
        # type: (...) -> None
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
        :type sample_rate: :obj:`float` greater than or equal to 0.0 and less than or equal to 1.0
        :param service: Rule to match the `span.service` on, default no rule defined
        :type service: :obj:`object` to directly compare, :obj:`function` to evaluate, or :class:`re.Pattern` to match
        :param name: Rule to match the `span.name` on, default no rule defined
        :type name: :obj:`object` to directly compare, :obj:`function` to evaluate, or :class:`re.Pattern` to match
        :param tags: A dictionary whose keys exactly match the names of tags expected to appear on spans, and whose
            values are glob-matches with the expected span tag values. Glob matching supports "*" meaning any
            number of characters, and "?" meaning any one character. If all tags specified in a SamplingRule are
            matches with a given span, that span is considered to have matching tags with the rule.
        """
        # Enforce sample rate constraints
        if not 0.0 <= sample_rate <= 1.0:
            raise ValueError(
                (
                    "SamplingRule(sample_rate={}) must be greater than or equal to 0.0 and less than or equal to 1.0"
                ).format(sample_rate)
            )

        self._tag_value_matchers = {k: GlobMatcher(v) for k, v in tags.items()} if tags != SamplingRule.NO_RULE else {}

        self.sample_rate = sample_rate
        self.service = service
        self.name = name
        self.resource = resource
        self.tags = tags

    @property
    def sample_rate(self):
        # type: () -> float
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, sample_rate):
        # type: (float) -> None
        self._sample_rate = sample_rate
        self._sampling_id_threshold = sample_rate * _MAX_UINT_64BITS

    def _pattern_matches(self, prop, pattern):
        # If the rule is not set, then assume it matches
        # DEV: Having no rule and being `None` are different things
        #   e.g. ignoring `span.service` vs `span.service == None`
        if pattern is self.NO_RULE:
            return True

        # If the pattern is callable (e.g. a function) then call it passing the prop
        #   The expected return value is a boolean so cast the response in case it isn't
        if callable(pattern):
            try:
                return bool(pattern(prop))
            except Exception:
                log.warning("%r pattern %r failed with %r", self, pattern, prop, exc_info=True)
                # Their function failed to validate, assume it is a False
                return False

        # The pattern is a regular expression and the prop is a string
        if isinstance(pattern, pattern_type):
            try:
                return bool(pattern.match(str(prop)))
            except (ValueError, TypeError):
                # This is to guard us against the casting to a string (shouldn't happen, but still)
                log.warning("%r pattern %r failed with %r", self, pattern, prop, exc_info=True)
                return False

        # Exact match on the values
        return prop == pattern

    @cachedmethod()
    def _matches(self, key):
        # type: (Tuple[Optional[str], str, Optional[str]]) -> bool
        # self._matches exists to maintain legacy pattern values such as regex and functions
        service, name, resource = key
        for prop, pattern in [(service, self.service), (name, self.name), (resource, self.resource)]:
            if not self._pattern_matches(prop, pattern):
                return False
        else:
            return True

    def matches(self, span):
        # type: (Span) -> bool
        """
        Return if this span matches this rule

        :param span: The span to match against
        :type span: :class:`ddtrace.span.Span`
        :returns: Whether this span matches or not
        :rtype: :obj:`bool`
        """
        glob_match = self.glob_matches(span)
        return glob_match and self._matches((span.service, span.name, span.resource))

    def glob_matches(self, span):
        # type: (Span) -> bool
        tag_match = True
        if self._tag_value_matchers:
            tag_match = self.tag_match(span.get_tags())
        return tag_match

    def tag_match(self, tags):
        if tags is None:
            return False

        tag_match = False
        for tag_key in self._tag_value_matchers.keys():
            value = tags.get(tag_key)
            if value is not None:
                tag_match = self._tag_value_matchers[tag_key].match(value)
            else:
                # if we don't match with all specified tags for a rule, it's not a match
                return False
        return tag_match

    def sample(self, span, allow_false=True):
        # type: (Span, bool) -> bool
        """
        Return if this rule chooses to sample the span

        :param span: The span to sample against
        :type span: :class:`ddtrace.span.Span`
        :returns: Whether this span was sampled
        :rtype: :obj:`bool`
        """
        if self.sample_rate == 1:
            return True
        elif self.sample_rate == 0:
            return False

        return (
            not allow_false
            or ((span._trace_id_64bits * KNUTH_FACTOR) % _MAX_UINT_64BITS) <= self._sampling_id_threshold
        )

    def _no_rule_or_self(self, val):
        return "NO_RULE" if val is self.NO_RULE else val

    def __repr__(self):
        return "{}(sample_rate={!r}, service={!r}, name={!r}, resource={!r}, tags={!r})".format(
            self.__class__.__name__,
            self.sample_rate,
            self._no_rule_or_self(self.service),
            self._no_rule_or_self(self.name),
            self._no_rule_or_self(self.resource),
            self._no_rule_or_self(self.tags),
        )

    __str__ = __repr__

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, SamplingRule):
            raise TypeError("Cannot compare SamplingRule to {}".format(type(other)))

        return (
            self.sample_rate == other.sample_rate
            and self.service == other.service
            and self.name == other.name
            and self.resource == other.resource
            and self.tags == other.tags
        )
