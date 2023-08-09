"""Samplers manage the client-side trace sampling

Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
"""
import abc
import json
import os
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import TYPE_CHECKING
from typing import Tuple
from typing import Union

import six

from .constants import AUTO_KEEP
from .constants import AUTO_REJECT
from .constants import ENV_KEY
from .constants import SAMPLING_AGENT_DECISION
from .constants import SAMPLING_LIMIT_DECISION
from .constants import SAMPLING_RULE_DECISION
from .constants import USER_KEEP
from .constants import USER_REJECT
from .internal.compat import iteritems
from .internal.compat import pattern_type
from .internal.constants import DEFAULT_SAMPLING_RATE_LIMIT
from .internal.constants import MAX_UINT_64BITS as _MAX_UINT_64BITS
from .internal.logger import get_logger
from .internal.rate_limiter import RateLimiter
from .internal.sampling import SamplingMechanism
from .internal.sampling import update_sampling_decision
from .internal.utils.cache import cachedmethod
from .settings import _config as ddconfig


try:
    from json.decoder import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

if TYPE_CHECKING:  # pragma: no cover
    from .span import Span


log = get_logger(__name__)

# All references to MAX_TRACE_ID were replaced with _MAX_UINT_64BITS.
# Now that ddtrace supports generating 128bit trace_ids,
# the max trace id should be 2**128 - 1 (not 2**64 -1)
# MAX_TRACE_ID is no longer used and should be removed.
MAX_TRACE_ID = _MAX_UINT_64BITS
# Has to be the same factor and key as the Agent to allow chained sampling
KNUTH_FACTOR = 1111111111111111111


class SamplingError(Exception):
    pass


class BaseSampler(six.with_metaclass(abc.ABCMeta)):

    __slots__ = ()

    @abc.abstractmethod
    def sample(self, span):
        pass


class BasePrioritySampler(BaseSampler):

    __slots__ = ()

    @abc.abstractmethod
    def update_rate_by_service_sample_rates(self, sample_rates):
        pass


class AllSampler(BaseSampler):
    """Sampler sampling all the traces"""

    def sample(self, span):
        # type: (Span) -> bool
        return True


class RateSampler(BaseSampler):
    """Sampler based on a rate

    Keep (100 * `sample_rate`)% of the traces.
    It samples randomly, its main purpose is to reduce the instrumentation footprint.
    """

    def __init__(self, sample_rate=1.0):
        # type: (float) -> None
        if sample_rate < 0.0:
            raise ValueError("sample_rate of {} is negative".format(sample_rate))
        elif sample_rate > 1.0:
            sample_rate = 1.0

        self.set_sample_rate(sample_rate)

        log.debug("initialized RateSampler, sample %s%% of traces", 100 * sample_rate)

    def set_sample_rate(self, sample_rate):
        # type: (float) -> None
        self.sample_rate = float(sample_rate)
        self.sampling_id_threshold = self.sample_rate * _MAX_UINT_64BITS

    def sample(self, span):
        # type: (Span) -> bool
        return ((span._trace_id_64bits * KNUTH_FACTOR) % _MAX_UINT_64BITS) <= self.sampling_id_threshold


class RateByServiceSampler(BasePrioritySampler):
    """Sampler based on a rate, by service

    Keep (100 * `sample_rate`)% of the traces.
    The sample rate is kept independently for each service/env tuple.
    """

    __slots__ = ("sample_rate", "_by_service_samplers", "_default_sampler")

    _default_key = "service:,env:"

    @staticmethod
    def _key(
        service=None,  # type: Optional[str]
        env=None,  # type: Optional[str]
    ):
        # type: (...) -> str
        """Compute a key with the same format used by the Datadog agent API."""
        service = service or ""
        env = env or ""
        return "service:" + service + ",env:" + env

    def __init__(self, sample_rate=1.0):
        # type: (float) -> None
        self.sample_rate = sample_rate
        self._default_sampler = RateSampler(self.sample_rate)
        self._by_service_samplers = {}  # type: Dict[str, RateSampler]

    def set_sample_rate(
        self,
        sample_rate,  # type: float
        service="",  # type: str
        env="",  # type: str
    ):
        # type: (...) -> None
        self._by_service_samplers[self._key(service, env)] = RateSampler(sample_rate)

    def _set_priority(self, span, priority):
        # type: (Span, int) -> None
        span.context.sampling_priority = priority
        span.sampled = priority > 0  # Positive priorities mean it was kept

    def _set_sampler_decision(self, span, sampler, sampled):
        # type: (Span, RateSampler, bool) -> None
        priority = AUTO_KEEP if sampled else AUTO_REJECT
        self._set_priority(span, priority)

        span.set_metric(SAMPLING_AGENT_DECISION, sampler.sample_rate)

        sampling_mechanism = (
            SamplingMechanism.DEFAULT if sampler == self._default_sampler else SamplingMechanism.AGENT_RATE
        )

        update_sampling_decision(span.context, sampling_mechanism, sampled)

    def sample(self, span):
        # type: (Span) -> bool
        env = span.get_tag(ENV_KEY)
        key = self._key(span.service, env)

        sampler = self._by_service_samplers.get(key) or self._default_sampler
        sampled = sampler.sample(span)
        self._set_sampler_decision(span, sampler, sampled)

        return sampled

    def update_rate_by_service_sample_rates(self, rate_by_service):
        # type: (Dict[str, float]) -> None
        samplers = {}
        for key, sample_rate in iteritems(rate_by_service):
            samplers[key] = RateSampler(sample_rate)

        self._by_service_samplers = samplers


class DatadogSampler(RateByServiceSampler):
    """
    Default sampler used by Tracer for determining if a trace should be kept or dropped.

    By default, this sampler will rely on dynamic sample rates provided by the trace agent
    to determine which traces are kept or dropped.

    You can also configure a static sample rate via ``default_sample_rate`` to use for sampling.
    When a ``default_sample_rate`` is configured, that is the only sample rate used, the agent
    provided rates are ignored.

    You may also supply a list of ``SamplingRule`` to determine sample rates for specific
    services or operation names.

    Example rules::

        DatadogSampler(rules=[
            SamplingRule(sample_rate=1.0, service="my-svc"),
            SamplingRule(sample_rate=0.0, service="less-important"),
        ])

    Rules are evaluated in the order they are provided, and the first rule that matches is used.
    If no rule matches, then the agent sample rates are used.


    Lastly, this sampler can be configured with a rate limit. This will ensure the max number of
    sampled traces per second does not exceed the supplied limit. The default is 100 traces kept
    per second. This rate limiter is only used when ``default_sample_rate`` or ``rules`` are
    provided. It is not used when the agent supplied sample rates are used.
    """

    __slots__ = ("limiter", "rules")

    NO_RATE_LIMIT = -1
    # deprecate and remove the DEFAULT_RATE_LIMIT field from DatadogSampler
    DEFAULT_RATE_LIMIT = DEFAULT_SAMPLING_RATE_LIMIT

    def __init__(
        self,
        rules=None,  # type: Optional[List[SamplingRule]]
        default_sample_rate=None,  # type: Optional[float]
        rate_limit=None,  # type: Optional[int]
    ):
        # type: (...) -> None
        """
        Constructor for DatadogSampler sampler

        :param rules: List of :class:`SamplingRule` rules to apply to the root span of every trace, default no rules
        :type rules: :obj:`list` of :class:`SamplingRule`
        :param default_sample_rate: The default sample rate to apply if no rules matched (default: ``None`` /
            Use :class:`RateByServiceSampler` only)
        :type default_sample_rate: float 0 <= X <= 1.0
        :param rate_limit: Global rate limit (traces per second) to apply to all traces regardless of the rules
            applied to them, (default: ``100``)
        :type rate_limit: :obj:`int`
        """
        # Use default sample rate of 1.0
        super(DatadogSampler, self).__init__()

        if default_sample_rate is None:
            sample_rate = ddconfig._trace_sample_rate

            if sample_rate is not None:
                default_sample_rate = float(sample_rate)

        if rate_limit is None:
            rate_limit = int(ddconfig._trace_rate_limit)

        if rules is None:
            env_sampling_rules = os.getenv("DD_TRACE_SAMPLING_RULES")
            if env_sampling_rules:
                rules = self._parse_rules_from_env_variable(env_sampling_rules)
            else:
                rules = []
            self.rules = rules
        else:
            self.rules = []
            # Validate that rules is a list of SampleRules
            for rule in rules:
                if not isinstance(rule, SamplingRule):
                    raise TypeError("Rule {!r} must be a sub-class of type ddtrace.sampler.SamplingRules".format(rule))
                self.rules.append(rule)

        # DEV: Default sampling rule must come last
        if default_sample_rate is not None:
            self.rules.append(SamplingRule(sample_rate=default_sample_rate))

        # Configure rate limiter
        self.limiter = RateLimiter(rate_limit)

        log.debug("initialized %r", self)

    def __str__(self):
        rates = {key: sampler.sample_rate for key, sampler in self._by_service_samplers.items()}
        return "{}(agent_rates={!r}, limiter={!r}, rules={!r})".format(
            self.__class__.__name__, rates, self.limiter, self.rules
        )

    __repr__ = __str__

    def _parse_rules_from_env_variable(self, rules):
        # type: (str) -> List[SamplingRule]
        sampling_rules = []
        try:
            json_rules = json.loads(rules)
        except JSONDecodeError:
            raise ValueError("Unable to parse DD_TRACE_SAMPLING_RULES={}".format(rules))
        for rule in json_rules:
            if "sample_rate" not in rule:
                raise KeyError("No sample_rate provided for sampling rule: {}".format(json.dumps(rule)))
            sample_rate = float(rule["sample_rate"])
            service = rule.get("service", SamplingRule.NO_RULE)
            name = rule.get("name", SamplingRule.NO_RULE)
            try:
                sampling_rule = SamplingRule(sample_rate=sample_rate, service=service, name=name)
            except ValueError as e:
                raise ValueError("Error creating sampling rule {}: {}".format(json.dumps(rule), e))
            sampling_rules.append(sampling_rule)
        return sampling_rules

    def _set_priority(self, span, priority):
        # type: (Span, int) -> None
        span.context.sampling_priority = priority
        span.sampled = priority > 0  # Positive priorities mean it was kept

    def _set_sampler_decision(self, span, sampler, sampled):
        # type: (Span, Union[RateSampler, SamplingRule, RateLimiter], bool) -> None
        if isinstance(sampler, RateSampler):
            # When agent based sampling is used
            return super(DatadogSampler, self)._set_sampler_decision(span, sampler, sampled)

        if isinstance(sampler, SamplingRule):
            span.set_metric(SAMPLING_RULE_DECISION, sampler.sample_rate)
        elif isinstance(sampler, RateLimiter) and not sampled:
            # We only need to set the rate limit metric if the limiter is rejecting the span
            # DEV: Setting this allows us to properly compute metrics and debug the
            #      various sample rates that are getting applied to this span
            span.set_metric(SAMPLING_LIMIT_DECISION, sampler.effective_rate)

        if not sampled:
            self._set_priority(span, USER_REJECT)
        else:
            self._set_priority(span, USER_KEEP)

        update_sampling_decision(span.context, SamplingMechanism.TRACE_SAMPLING_RULE, sampled)

    def sample(self, span):
        # type: (Span) -> bool
        """
        Decide whether the provided span should be sampled or not

        The span provided should be the root span in the trace.

        :param span: The root span of a trace
        :type span: :class:`ddtrace.span.Span`
        :returns: Whether the span was sampled or not
        :rtype: :obj:`bool`
        """
        # Go through all rules and grab the first one that matched
        # DEV: This means rules should be ordered by the user from most specific to least specific
        for rule in self.rules:
            if rule.matches(span):
                sampler = rule
                break
        else:
            # No rules match so use agent based sampling
            return super(DatadogSampler, self).sample(span)

        sampled = sampler.sample(span)
        self._set_sampler_decision(span, sampler, sampled)

        if sampled:
            # Ensure all allowed traces adhere to the global rate limit
            allowed = self.limiter.is_allowed(span.start_ns)
            if not allowed:
                self._set_sampler_decision(span, self.limiter, allowed)
                return False

        return sampled


class SamplingRule(BaseSampler):
    """
    Definition of a sampling rule used by :class:`DatadogSampler` for applying a sample rate on a span
    """

    NO_RULE = object()

    def __init__(
        self,
        sample_rate,  # type: float
        service=NO_RULE,  # type: Any
        name=NO_RULE,  # type: Any
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
        """
        # Enforce sample rate constraints
        if not 0.0 <= sample_rate <= 1.0:
            raise ValueError(
                (
                    "SamplingRule(sample_rate={}) must be greater than or equal to 0.0 and less than or equal to 1.0"
                ).format(sample_rate)
            )

        self.sample_rate = sample_rate
        self.service = service
        self.name = name

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
        # type: (Tuple[Optional[str], str]) -> bool
        service, name = key
        for prop, pattern in [(service, self.service), (name, self.name)]:
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
        # Our LFU cache expects a single key, convert the
        # provided Span into a hashable tuple for the cache
        return self._matches((span.service, span.name))

    def sample(self, span):
        # type: (Span) -> bool
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

        return ((span._trace_id_64bits * KNUTH_FACTOR) % _MAX_UINT_64BITS) <= self._sampling_id_threshold

    def _no_rule_or_self(self, val):
        return "NO_RULE" if val is self.NO_RULE else val

    def __repr__(self):
        return "{}(sample_rate={!r}, service={!r}, name={!r})".format(
            self.__class__.__name__,
            self.sample_rate,
            self._no_rule_or_self(self.service),
            self._no_rule_or_self(self.name),
        )

    __str__ = __repr__

    def __eq__(self, other):
        # type: (Any) -> bool
        if not isinstance(other, SamplingRule):
            raise TypeError("Cannot compare SamplingRule to {}".format(type(other)))

        return self.sample_rate == other.sample_rate and self.service == other.service and self.name == other.name
