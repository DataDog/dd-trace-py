"""Samplers manage the client-side trace sampling

Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
"""
import abc
from typing import Dict
from typing import List
from typing import Optional
from typing import TYPE_CHECKING
from typing import Union

import six

from ddtrace.internal.constants import SAMPLING_DECISION_TRACE_TAG_KEY

from .constants import AUTO_KEEP
from .constants import AUTO_REJECT
from .constants import ENV_KEY
from .constants import SAMPLING_AGENT_DECISION
from .constants import SAMPLING_LIMIT_DECISION
from .constants import SAMPLING_PRIORITY_KEY
from .constants import SAMPLING_RULE_DECISION
from .constants import USER_KEEP
from .constants import USER_REJECT
from .internal.compat import iteritems
from .internal.constants import DEFAULT_SAMPLING_RATE_LIMIT
from .internal.constants import MAX_UINT_64BITS as _MAX_UINT_64BITS
from .internal.logger import get_logger
from .internal.rate_limiter import RateLimiter
from .internal.sampling import SamplingMechanism
from .internal.sampling import SamplingRule
from .internal.sampling import set_sampling_decision_maker
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

    def sample(self, span, is_being_default=False):
        # type: (Span) -> bool
        if isinstance(span, list):
            span = span[0]
        sampled = ((span._trace_id_64bits * KNUTH_FACTOR) % _MAX_UINT_64BITS) <= self.sampling_id_threshold
        span.sampled = sampled
        if not is_being_default:
            priority = USER_KEEP if sampled else USER_REJECT
            span.set_metric(SAMPLING_PRIORITY_KEY, priority)
            span.context.sampling_priority = priority
            span.set_metric(SAMPLING_RULE_DECISION, self.sample_rate)
            set_sampling_decision_maker(span.context, SamplingMechanism.TRACE_SAMPLING_RULE)
        return sampled


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

    @classmethod
    def from_datadog_sampler(cls, ddsampler):
        instance = cls(sample_rate=ddsampler.sample_rate)
        instance._by_service_samplers = ddsampler._by_service_samplers
        return instance

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
        span.sampled = priority > 0  # Positive priorities mean it was kept
        span.set_metric(SAMPLING_PRIORITY_KEY, priority)
        span.context.sampling_priority = priority

    def _set_sampler_decision(self, span, sampler, sampled, has_remote_root):
        # type: (Span, RateSampler, bool, bool, bool) -> None
        priority, sampled, sampling_mechanism = _apply_sampling_overrides(span, sampler, sampled, self._default_sampler)
        self._set_priority(span, priority)
        if not has_remote_root and sampler is not self._default_sampler:
            span.set_metric(SAMPLING_AGENT_DECISION, sampler.sample_rate)
        set_sampling_decision_maker(span.context, sampling_mechanism)

    def sample(self, trace):
        # type: (List[Span]) -> bool
        chunk_root = trace[0]
        env = chunk_root.get_tag(ENV_KEY)
        key = self._key(chunk_root.service, env)

        sampler = self._by_service_samplers.get(key) or self._default_sampler
        sampled = sampler.sample(chunk_root, is_being_default=self._default_sampler is sampler)
        has_local_root = any(chunk_root.span_id == a.parent_id for a in trace)
        has_remote_root = not has_local_root and chunk_root.parent_id is not None
        self._set_sampler_decision(chunk_root, sampler, sampled, has_remote_root)

        return sampled

    def update_rate_by_service_sample_rates(self, rate_by_service):
        # type: (Dict[str, float]) -> None
        samplers = {}
        for key, sample_rate in iteritems(rate_by_service):
            samplers[key] = RateSampler(sample_rate)

        self._by_service_samplers = samplers


def _apply_sampling_overrides(span, sampler, sampled, _default_sampler):
    span_priority = span._metrics.get(SAMPLING_PRIORITY_KEY)
    context_priority = span.context.sampling_priority
    if context_priority in (USER_KEEP, USER_REJECT):
        sampled = context_priority > 0

    if context_priority not in (USER_REJECT, AUTO_REJECT):
        if sampled:
            context_priority = AUTO_KEEP
        elif not sampled:
            context_priority = AUTO_REJECT

    sampling_mechanism = span.context._meta.get(SAMPLING_DECISION_TRACE_TAG_KEY)
    if sampling_mechanism:
        sampling_mechanism = int(sampling_mechanism[1:])
    if not sampling_mechanism:
        if span.context.sampling_priority in (USER_KEEP, USER_REJECT):
            sampling_mechanism = SamplingMechanism.MANUAL
        elif sampler is _default_sampler:
            sampling_mechanism = SamplingMechanism.DEFAULT
        else:
            sampling_mechanism = SamplingMechanism.AGENT_RATE
    return span_priority or context_priority, sampled, sampling_mechanism


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
        compute_stats=False,
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

        rules = rules or []
        self.rules = [rule for rule in rules if isinstance(rule, SamplingRule)]
        if len(self.rules) != len(rules):
            raise TypeError("Sampling rules must be objects of type ddtrace.sampler.SamplingRule")

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

    def _set_sampler_decision(self, span, sampler, sampled, has_remote_root):
        # type: (Span, Union[RateSampler, SamplingRule, RateLimiter], bool, bool, bool) -> None
        priority, sampled, sampling_mechanism = _apply_sampling_overrides(span, sampler, sampled, self._default_sampler)
        set_sampling_decision_maker(span.context, SamplingMechanism.TRACE_SAMPLING_RULE)
        if sampling_mechanism != SamplingMechanism.MANUAL and isinstance(sampler, SamplingRule):
            span.set_metric(SAMPLING_RULE_DECISION, sampler.sample_rate)

        self._set_priority(span, USER_KEEP if priority > 0 else USER_REJECT)

    def find_highest_precedence_rule_matching(self, trace):
        # type: (List[Span]) -> Optional[SamplingRule]
        if not self.rules:
            return None
        rule_decision = [0 for _ in self.rules]

        # need to check span context object tags since they haven't yet been added to the root span.
        context = trace[0].context
        for rule in self.rules:
            for tag in context._meta:
                if rule.tag_match(context._meta):
                    rule_decision[self.rules.index(rule)] += 1
                    break
            for metric in context._metrics:
                if rule.tag_match(context._metrics):
                    rule_decision[self.rules.index(rule)] += 1
                    break
            for span in trace:
                if span._trace_sampling_checked:
                    continue
                if rule.matches(span):
                    rule_decision[self.rules.index(rule)] += 1

        for index, value in enumerate(rule_decision):
            if value > 0:
                return self.rules[index]
        return None

    def sample(self, trace):
        # type: (List[Span]) -> bool
        chunk_root = trace[0]
        has_configured_rate_limit = self.limiter.rate_limit != DEFAULT_SAMPLING_RATE_LIMIT
        has_local_root = any(chunk_root.span_id == a.parent_id for a in trace)
        has_remote_root = not has_local_root and chunk_root.parent_id is not None
        rule = self.find_highest_precedence_rule_matching(trace)
        if rule:
            sampler = rule
        else:
            sampler = RateByServiceSampler.from_datadog_sampler(self)

        sampled = sampler.sample(trace)

        if rule or has_configured_rate_limit:
            self._set_sampler_decision(chunk_root, sampler, sampled, has_remote_root)

        # Ensure all allowed traces adhere to the global rate limit
        allowed = True
        if sampled:
            allowed = self.limiter.is_allowed(chunk_root.start_ns)
            if not allowed:
                self._set_priority(chunk_root, USER_REJECT)
        if has_configured_rate_limit:
            chunk_root.set_metric(SAMPLING_LIMIT_DECISION, self.limiter.effective_rate)
        return not allowed or sampled
