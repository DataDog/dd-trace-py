"""Samplers manage the client-side trace sampling

Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
"""
import abc

from .compat import iteritems, pattern_type
from .ext.priority import AUTO_KEEP, AUTO_REJECT
from .internal.logger import get_logger
from .internal.rate_limiter import RateLimiter
from .vendor import six

log = get_logger(__name__)

MAX_TRACE_ID = 2 ** 64

# Has to be the same factor and key as the Agent to allow chained sampling
KNUTH_FACTOR = 1111111111111111111


class BaseSampler(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def sample(self, span):
        pass


class AllSampler(BaseSampler):
    """Sampler sampling all the traces"""

    def sample(self, span):
        return True


class RateSampler(BaseSampler):
    """Sampler based on a rate

    Keep (100 * `sample_rate`)% of the traces.
    It samples randomly, its main purpose is to reduce the instrumentation footprint.
    """

    def __init__(self, sample_rate=1):
        if sample_rate <= 0:
            log.error('sample_rate is negative or null, disable the Sampler')
            sample_rate = 1
        elif sample_rate > 1:
            sample_rate = 1

        self.set_sample_rate(sample_rate)

        log.debug('initialized RateSampler, sample %s%% of traces', 100 * sample_rate)

    def set_sample_rate(self, sample_rate):
        self.sample_rate = sample_rate
        self.sampling_id_threshold = sample_rate * MAX_TRACE_ID

    def sample(self, span):
        sampled = ((span.trace_id * KNUTH_FACTOR) % MAX_TRACE_ID) <= self.sampling_id_threshold

        return sampled


class RateByServiceSampler(BaseSampler):
    """Sampler based on a rate, by service

    Keep (100 * `sample_rate`)% of the traces.
    The sample rate is kept independently for each service/env tuple.
    """

    @staticmethod
    def _key(service=None, env=None):
        """Compute a key with the same format used by the Datadog agent API."""
        service = service or ''
        env = env or ''
        return 'service:' + service + ',env:' + env

    def __init__(self, sample_rate=1):
        self.sample_rate = sample_rate
        self._by_service_samplers = self._get_new_by_service_sampler()

    def _get_new_by_service_sampler(self):
        return {
            self._default_key: RateSampler(self.sample_rate)
        }

    def set_sample_rate(self, sample_rate, service='', env=''):
        self._by_service_samplers[self._key(service, env)] = RateSampler(sample_rate)

    def sample(self, span):
        tags = span.tracer().tags
        env = tags['env'] if 'env' in tags else None
        key = self._key(span.service, env)
        return self._by_service_samplers.get(
            key, self._by_service_samplers[self._default_key]
        ).sample(span)

    def set_sample_rate_by_service(self, rate_by_service):
        new_by_service_samplers = self._get_new_by_service_sampler()
        for key, sample_rate in iteritems(rate_by_service):
            new_by_service_samplers[key] = RateSampler(sample_rate)

        self._by_service_samplers = new_by_service_samplers


# Default key for service with no specific rate
RateByServiceSampler._default_key = RateByServiceSampler._key()


class DatadogSampler(BaseSampler):
    """
    """
    __slots__ = ('rules', 'rate_limit')

    DEFAULT_RATE_LIMIT = 100

    def __init__(self, rules=None, rate_limit=None):
        """
        Constructor for DatadogSampler sampler

        :param rules: List of :class:`SamplingRule` rules to apply to the root span of every trace, default no rules
        :type rules: :obj:`list` of :class:`SamplingRule`
        :param rate_limit: Global rate limit (traces per second) to apply to all traces regardless of the rules
            applied to them, default 100 traces per second
        :type rate_limit: :obj:`int`
        """
        # Ensure rules is a list
        if not rules:
            rules = []

        # Validate that the rules is a list of SampleRules
        for rule in rules:
            if not isinstance(rule, SamplingRule):
                raise TypeError('Rule {!r} must be a sub-class of type ddtrace.sampler.SamplingRules'.format(rule))
        self.rules = rules

        # Configure rate limiter
        if rate_limit is None:
            rate_limit = self.DEFAULT_RATE_LIMIT
        self.limiter = RateLimiter(rate_limit)

    def sample(self, span):
        """
        Decide whether the provided span should be sampled or not

        The span provided should be the root span in the trace.

        :param span: The root span of a trace
        :type span: :class:`ddtrace.span.Span`
        :returns: Whether the span was sampled or not
        :rtype: :obj:`bool`
        """
        # If there are rules defined, then iterate through them and find one that wants to sample
        sampled_by_rule = None
        if self.rules:
            # Go through all rules and grab the last one that matched
            # DEV: This means rules should be ordered by the user from least specific to most specific
            for rule in self.rules:
                if rule.should_sample(span):
                    sampled_by_rule = rule

            # If no rule wanted to sample this, then do not sample
            if not sampled_by_rule:
                span._context.sampling_priority = AUTO_REJECT
                return False

        # Ensure all allowed traces adhere to the global rate limit
        if not self.limiter.is_allowed():
            span._context.sampling_priority = AUTO_REJECT
            return False

        # TODO: Set `_sampling_priority_rate_v1` tag based on
        #       sampled_by_rule.sample_rate and effective rate from self.limiter
        # We made it by all of checks, sample this trace
        span._context.sampling_priority = AUTO_KEEP
        return True


class SamplingRule(object):
    """
    Definition of a sampling rule used by :class:`DatadogSampler` for applying a sample rate on a span
    """
    __slots__ = ('_sample_rate', '_sampling_id_threshold', 'service', 'name', 'resource', 'tags')

    NO_RULE = object()

    def __init__(self, sample_rate, service=NO_RULE, name=NO_RULE, resource=NO_RULE, tags=NO_RULE):
        """
        Configure a new :class:`SamplingRule`

        .. code:: python

            DatadogSampler([
                # Sample 100% of all traces by default
                SamplingRule(sample_rate=1.0),

                # Sample no healthcheck traces
                SamplingRule(sample_rate=0, name='flask.request', resource='GET /healthcheck'),

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
        :param resource: Rule to match the `span.resource` on, default no rule defined
        :type resource: :obj:`object` to directly compare, :obj:`function` to evaluate, or :class:`re.Pattern` to match
        :param tags: Dictionary of tag -> pattern to match on the `span.meta`, default no rule defined
        :type tags: :obj:`dict` mapping :obj:`str` tag names to either :obj:`object` to directly compare,
            :obj:`function` to evaluate, or :class:`re.Pattern` to match
        """
        # Enforce sample rate constraints
        if not 0.0 <= sample_rate <= 1.0:
            raise ValueError(
                'SamplingRule(sample_rate={!r}) must be greater than or equal to 0.0 and less than or equal to 1.0',
            )

        self.sample_rate = sample_rate
        self.service = service
        self.name = name
        self.resource = resource

        if tags is not self.NO_RULE and not isinstance(tags, dict):
            raise TypeError('SamplingRule(tags={!r}) must be a dict')
        self.tags = tags

    @property
    def sample_rate(self):
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, sample_rate):
        self._sample_rate = sample_rate
        self._sampling_id_threshold = sample_rate * MAX_TRACE_ID

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
                # TODO: Log that the pattern function failed
                # Their function failed to validate, assume it is a False
                return False

        # The pattern is a regular expression and the prop is a string
        if isinstance(pattern, pattern_type):
            try:
                return bool(pattern.match(str(prop)))
            except (ValueError, TypeError):
                # This is to guard us against the casting to a string (shouldn't happen, but still)
                # TODO: Log that we could not apply the pattern to the prop?
                return False

        # Exact match on the values
        return prop == pattern

    def _tags_match(self, span):
        # No rule was set, then assume it matches
        if self.tags is self.NO_RULE:
            return True

        # No tags were provided to check, so it passes
        if not self.tags:
            return True

        # Check patterns from all tags
        return all(
            self._pattern_matches(span.get_tag(tag), pattern)
            for tag, pattern in self.tags.items()
        )

    def should_sample(self, span):
        """
        Return if this rule chooses to sample the span

        :param span: The span to sample against
        :type span: :class:`ddtrace.span.Span`
        :returns: Whether this span matches and was sampled
        :rtype: :obj:`bool`
        """
        # make sure service, name, and resource match
        if not all(
            self._pattern_matches(prop, pattern)
            for prop, pattern in [
                    (span.service, self.service),
                    (span.name, self.name),
                    (span.resource, self.resource),
            ]
        ):
            return False

        # Make sure tags match
        if not self._tags_match(span):
            return False

        # All patterns match, check against our sample rate
        return self._sample(span)

    def _sample(self, span):
        if self.sample_rate == 1:
            return True
        elif self.sample_rate == 0:
            return False

        return ((span.trace_id * KNUTH_FACTOR) % MAX_TRACE_ID) <= self._sampling_id_threshold

    def _no_rule_or_self(self, val):
        return 'NO_RULE' if val is self.NO_RULE else val

    def __repr__(self):
        return '{}(sample_rate={!r}, service={!r}, name={!r}, resource={!r}, tags={!r})'.format(
            self.__class__.__name__,
            self.sample_rate,
            self._no_rule_or_self(self.service),
            self._no_rule_or_self(self.name),
            self._no_rule_or_self(self.resource),
            self._no_rule_or_self(self.tags),
        )

    __str__ = __repr__
