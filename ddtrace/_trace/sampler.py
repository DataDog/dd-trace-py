"""Samplers manage the client-side trace sampling

Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
"""
import json
from typing import TYPE_CHECKING  # noqa:F401
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401

from ddtrace import config
from ddtrace.constants import _SAMPLING_LIMIT_DECISION

from ..constants import ENV_KEY
from ..internal.constants import DEFAULT_SAMPLING_RATE_LIMIT
from ..internal.constants import MAX_UINT_64BITS as _MAX_UINT_64BITS
from ..internal.constants import SamplingMechanism
from ..internal.logger import get_logger
from ..internal.rate_limiter import RateLimiter
from ..internal.sampling import _get_highest_precedence_rule_matching
from ..internal.sampling import _set_sampling_tags
from .sampling_rule import SamplingRule


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


class RateSampler:
    """Sampler based on a rate

    Keep (100 * `sample_rate`)% of the traces.
    It samples randomly, its main purpose is to reduce the instrumentation footprint.
    """

    def __init__(self, sample_rate: float = 1.0) -> None:
        """sample_rate is clamped between 0 and 1 inclusive"""
        sample_rate = min(1, max(0, sample_rate))
        self.set_sample_rate(sample_rate)
        log.debug("initialized RateSampler, sample %s%% of traces", 100 * sample_rate)

    def set_sample_rate(self, sample_rate: float) -> None:
        self.sample_rate = float(sample_rate)
        self.sampling_id_threshold = self.sample_rate * _MAX_UINT_64BITS

    def sample(self, span):
        sampled = ((span._trace_id_64bits * KNUTH_FACTOR) % _MAX_UINT_64BITS) <= self.sampling_id_threshold
        return sampled


class DatadogSampler:
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

    __slots__ = (
        "limiter",
        "rules",
        "default_sample_rate",
        "_rate_limit_always_on",
        "_by_service_samplers",
    )
    _default_key = "service:,env:"

    NO_RATE_LIMIT = -1
    # deprecate and remove the DEFAULT_RATE_LIMIT field from DatadogSampler
    DEFAULT_RATE_LIMIT = DEFAULT_SAMPLING_RATE_LIMIT

    def __init__(
        self,
        rules=None,  # type: Optional[List[SamplingRule]]
        default_sample_rate=None,  # type: Optional[float]
        rate_limit=None,  # type: Optional[int]
        rate_limit_window=1e9,  # type: float
        rate_limit_always_on=False,  # type: bool
    ):
        # type: (...) -> None
        """
        Constructor for DatadogSampler sampler

        :param rules: List of :class:`SamplingRule` rules to apply to the root span of every trace, default no rules
        :param default_sample_rate: The default sample rate to apply if no rules matched
        :param rate_limit: Global rate limit (traces per second) to apply to all traces regardless of the rules
            applied to them, (default: ``100``)
        """
        self._by_service_samplers = {}  # type: Dict[str, RateSampler]

        self.default_sample_rate = default_sample_rate
        effective_sample_rate = default_sample_rate
        if default_sample_rate is None:
            if config._get_source("_trace_sample_rate") != "default":
                effective_sample_rate = float(config._trace_sample_rate)

        if rate_limit is None:
            rate_limit = int(config._trace_rate_limit)

        self._rate_limit_always_on = rate_limit_always_on

        self.rules: List[SamplingRule] = []
        if rules is None:
            env_sampling_rules = config._trace_sampling_rules
            if env_sampling_rules:
                self.rules = self._parse_rules_from_str(env_sampling_rules)
        else:
            # Validate that rules is a list of SampleRules
            for rule in rules:
                if isinstance(rule, SamplingRule):
                    self.rules.append(rule)
                elif config._raise:
                    raise TypeError(
                        "Rule {!r} must be a sub-class of type ddtrace._trace.sampler.SamplingRules".format(rule)
                    )

        # DEV: sampling rule must come last
        if effective_sample_rate is not None:
            self.rules.append(SamplingRule(sample_rate=effective_sample_rate))

        # Configure rate limiter
        self.limiter = RateLimiter(rate_limit, rate_limit_window)

        log.debug("initialized %r", self)

    @staticmethod
    def _key(service: Optional[str], env: Optional[str]):
        """Compute a key with the same format used by the Datadog agent API."""
        return f"service:{service or ''},env:{env or ''}"

    def update_rate_by_service_sample_rates(self, rate_by_service):
        # type: (Dict[str, float]) -> None
        samplers = {}  # type: Dict[str, RateSampler]
        for key, sample_rate in rate_by_service.items():
            samplers[key] = RateSampler(sample_rate)
        self._by_service_samplers = samplers

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
        json_rules = []
        try:
            json_rules = json.loads(rules)
        except JSONDecodeError:
            if config._raise:
                raise ValueError("Unable to parse DD_TRACE_SAMPLING_RULES={}".format(rules))
        for rule in json_rules:
            if "sample_rate" not in rule:
                if config._raise:
                    raise KeyError("No sample_rate provided for sampling rule: {}".format(json.dumps(rule)))
                continue
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
                if config._raise:
                    raise ValueError("Error creating sampling rule {}: {}".format(json.dumps(rule), e))
                continue
            sampling_rules.append(sampling_rule)

        # Sort the sampling_rules list using a lambda function as the key
        sampling_rules = sorted(sampling_rules, key=lambda rule: PROVENANCE_ORDER.index(rule.provenance))
        return sampling_rules

    def sample(self, span):
        span.context._update_tags(span)
        matched_rule = _get_highest_precedence_rule_matching(span, self.rules)
        # Default sampling
        agent_service_based = False
        sampled = True
        sample_rate = 1.0
        if matched_rule:
            # Rules based sampling (set via env_var or remote config)
            sampled = matched_rule.sample(span)
            sample_rate = matched_rule.sample_rate
        else:
            key = self._key(span.service, span.get_tag(ENV_KEY))
            if key in self._by_service_samplers:
                # Agent service based sampling
                agent_service_based = True
                sampled = self._by_service_samplers[key].sample(span)
                sample_rate = self._by_service_samplers[key].sample_rate

        if matched_rule or self._rate_limit_always_on:
            # Avoid rate limiting when trace sample rules and/or sample rates are NOT provided
            # by users. In this scenario tracing should default to agent based sampling. ASM
            # uses DatadogSampler._rate_limit_always_on to override this functionality.
            if sampled:
                sampled = self.limiter.is_allowed()
                span.set_metric(_SAMPLING_LIMIT_DECISION, self.limiter.effective_rate)

        sampling_mechanism = self._get_sampling_mechanism(matched_rule, agent_service_based)
        _set_sampling_tags(
            span,
            sampled,
            sample_rate,
            sampling_mechanism,
        )
        return sampled

    def _get_sampling_mechanism(self, matched_rule, agent_service_based):
        # type: (Optional[SamplingRule], bool) -> int
        if matched_rule and matched_rule.provenance == "customer":
            return SamplingMechanism.REMOTE_USER_TRACE_SAMPLING_RULE
        elif matched_rule and matched_rule.provenance == "dynamic":
            return SamplingMechanism.REMOTE_DYNAMIC_TRACE_SAMPLING_RULE
        elif matched_rule:
            return SamplingMechanism.LOCAL_USER_TRACE_SAMPLING_RULE
        elif self._rate_limit_always_on:
            # backwards compaitbiility for ASM, when the rate limit is always on (ASM standalone mode)
            # we want spans to be set to a MANUAL priority to avoid agent based sampling
            return SamplingMechanism.MANUAL
        elif agent_service_based:
            return SamplingMechanism.AGENT_RATE_BY_SERVICE
        else:
            return SamplingMechanism.DEFAULT
