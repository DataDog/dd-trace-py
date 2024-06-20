"""Samplers manage the client-side trace sampling

Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
"""
import abc
import json
from typing import TYPE_CHECKING  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import Tuple  # noqa:F401

from ddtrace.constants import SAMPLING_LIMIT_DECISION

from .constants import ENV_KEY
from .internal.constants import _PRIORITY_CATEGORY
from .internal.constants import DEFAULT_SAMPLING_RATE_LIMIT
from .internal.constants import MAX_UINT_64BITS as _MAX_UINT_64BITS
from .internal.logger import get_logger
from .internal.rate_limiter import RateLimiter
from .internal.sampling import _get_highest_precedence_rule_matching
from .internal.sampling import _set_sampling_tags
from .sampling_rule import SamplingRule
from .settings import _config as ddconfig


PROVENANCE_ORDER = ["customer", "dynamic", "default"]

try:
    from json.decoder import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

if TYPE_CHECKING:  # pragma: no cover
    from ddtrace._trace.span import Span  # noqa:F401


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


class BaseSampler(metaclass=abc.ABCMeta):
    __slots__ = ()

    @abc.abstractmethod
    def sample(self, span):
        # type: (Span) -> bool
        pass


class BasePrioritySampler(BaseSampler):
    __slots__ = ()

    @abc.abstractmethod
    def update_rate_by_service_sample_rates(self, sample_rates):
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
        sampled = ((span._trace_id_64bits * KNUTH_FACTOR) % _MAX_UINT_64BITS) <= self.sampling_id_threshold
        return sampled


class _AgentRateSampler(RateSampler):
    pass


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
        self._by_service_samplers[self._key(service, env)] = _AgentRateSampler(sample_rate)

    def sample(self, span):
        sampled, sampler = self._make_sampling_decision(span)
        _set_sampling_tags(
            span,
            sampled,
            sampler.sample_rate,
            self._choose_priority_category(sampler),
        )
        return sampled

    def _choose_priority_category(self, sampler):
        # type: (BaseSampler) -> str
        if sampler is self._default_sampler:
            return _PRIORITY_CATEGORY.DEFAULT
        elif isinstance(sampler, _AgentRateSampler):
            return _PRIORITY_CATEGORY.AUTO
        else:
            return _PRIORITY_CATEGORY.RULE_DEF

    def _make_sampling_decision(self, span):
        # type: (Span) -> Tuple[bool, BaseSampler]
        env = span.get_tag(ENV_KEY)
        key = self._key(span.service, env)
        sampler = self._by_service_samplers.get(key) or self._default_sampler
        sampled = sampler.sample(span)
        return sampled, sampler

    def update_rate_by_service_sample_rates(self, rate_by_service):
        # type: (Dict[str, float]) -> None
        samplers = {}  # type: Dict[str, RateSampler]
        for key, sample_rate in rate_by_service.items():
            samplers[key] = _AgentRateSampler(sample_rate)

        self._by_service_samplers = samplers


class DatadogSampler(RateByServiceSampler):
    """
    By default, this sampler relies on dynamic sample rates provided by the trace agent
    to determine which traces are kept or dropped.

    You can also configure a static sample rate via ``default_sample_rate`` to use for sampling.
    When a ``default_sample_rate`` is configured, that is the only sample rate used, and the agent
    provided rates are ignored.

    You may also supply a list of ``SamplingRule`` instances to set sample rates for specific
    services.

    Example rules::

        DatadogSampler(rules=[
            SamplingRule(sample_rate=1.0, service="my-svc"),
            SamplingRule(sample_rate=0.0, service="less-important"),
        ])

    Rules are evaluated in the order they are provided, and the first rule that matches is used.
    If no rule matches, then the agent sample rates are used.

    This sampler can be configured with a rate limit. This will ensure the max number of
    sampled traces per second does not exceed the supplied limit. The default is 100 traces kept
    per second.
    """

    __slots__ = ("limiter", "rules", "default_sample_rate")

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
        :param default_sample_rate: The default sample rate to apply if no rules matched (default: ``None`` /
            Use :class:`RateByServiceSampler` only)
        :param rate_limit: Global rate limit (traces per second) to apply to all traces regardless of the rules
            applied to them, (default: ``100``)
        """
        # Use default sample rate of 1.0
        super(DatadogSampler, self).__init__()
        self.default_sample_rate = default_sample_rate
        if default_sample_rate is None:
            if ddconfig._get_source("_trace_sample_rate") != "default":
                default_sample_rate = float(ddconfig._trace_sample_rate)

        if rate_limit is None:
            rate_limit = int(ddconfig._trace_rate_limit)

        if rules is None:
            env_sampling_rules = ddconfig._trace_sampling_rules
            if env_sampling_rules:
                rules = self._parse_rules_from_str(env_sampling_rules)
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

    @staticmethod
    def _parse_rules_from_str(rules):
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
            resource = rule.get("resource", SamplingRule.NO_RULE)
            tags = rule.get("tags", SamplingRule.NO_RULE)
            provenance = rule.get("provenance", "default")
            try:
                sampling_rule = SamplingRule(
                    sample_rate=sample_rate,
                    service=service,
                    name=name,
                    resource=resource,
                    tags=tags,
                    provenance=provenance,
                )
            except ValueError as e:
                raise ValueError("Error creating sampling rule {}: {}".format(json.dumps(rule), e))
            sampling_rules.append(sampling_rule)

        # Sort the sampling_rules list using a lambda function as the key
        sampling_rules = sorted(sampling_rules, key=lambda rule: PROVENANCE_ORDER.index(rule.provenance))
        return sampling_rules

    def sample(self, span):
        span.context._update_tags(span)

        matched_rule = _get_highest_precedence_rule_matching(span, self.rules)

        sampler = self._default_sampler  # type: BaseSampler
        sample_rate = self.sample_rate
        if matched_rule:
            sampled = matched_rule.sample(span)
            sample_rate = matched_rule.sample_rate
        else:
            sampled, sampler = super(DatadogSampler, self)._make_sampling_decision(span)
            if isinstance(sampler, RateSampler):
                sample_rate = sampler.sample_rate
        # Apply rate limit
        if sampled:
            sampled = self.limiter.is_allowed()
        if self.limiter._has_been_configured:
            span.set_metric(SAMPLING_LIMIT_DECISION, self.limiter.effective_rate)

        _set_sampling_tags(
            span,
            sampled,
            sample_rate,
            self._choose_priority_category_with_rule(matched_rule, sampler),
        )

        return sampled

    def _choose_priority_category_with_rule(self, rule, sampler):
        # type: (Optional[SamplingRule], BaseSampler) -> str
        if rule:
            provenance = rule.provenance
            if provenance == "customer":
                return _PRIORITY_CATEGORY.RULE_CUSTOMER
            if provenance == "dynamic":
                return _PRIORITY_CATEGORY.RULE_DYNAMIC
            return _PRIORITY_CATEGORY.RULE_DEF

        if self.limiter._has_been_configured:
            # If the default rate limiter is NOT used to sample traces
            # the sampling priority must be set to manual keep/drop.
            # This will disable agent based sample rates.
            return _PRIORITY_CATEGORY.USER
        return super(DatadogSampler, self)._choose_priority_category(sampler)
