"""Samplers manage the client-side trace sampling

Any `sampled = False` trace won't be written, and can be ignored by the instrumentation.
"""
import json
from json.decoder import JSONDecodeError
from typing import Dict
from typing import List
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.constants import _SAMPLING_LIMIT_DECISION
from ddtrace.settings._config import config

from ..constants import ENV_KEY
from ..internal.constants import MAX_UINT_64BITS
from ..internal.constants import SAMPLING_HASH_MODULO
from ..internal.constants import SAMPLING_KNUTH_FACTOR
from ..internal.constants import SamplingMechanism
from ..internal.logger import get_logger
from ..internal.rate_limiter import RateLimiter
from ..internal.sampling import _get_highest_precedence_rule_matching
from ..internal.sampling import _set_sampling_tags
from .sampling_rule import SamplingRule


PROVENANCE_ORDER = ["customer", "dynamic", "default"]


log = get_logger(__name__)


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
        self.sampling_id_threshold = self.sample_rate * MAX_UINT_64BITS

    def sample(self, span: Span) -> bool:
        sampled = ((span._trace_id_64bits * SAMPLING_KNUTH_FACTOR) % SAMPLING_HASH_MODULO) <= self.sampling_id_threshold
        return sampled


class DatadogSampler:
    """
    The DatadogSampler samples traces based on the following (in order of precedence):
       - A list of sampling rules, applied in the order they are provided. The first matching rule is used.
       - A default sample rate, stored as the final sampling rule (lowest precedence sampling rule).
       - A global rate limit, applied only if a rule is matched or if `rate_limit_always_on` is set to `True`.
       - Sample rates provided by the agent (priority sampling, maps sample rates to service and env tags).
       - By default, spans are sampled at a rate of 1.0 and assigned an `AUTO_KEEP` priority, allowing
         the agent to determine the final sample rate and sampling decision.

    Example sampling rules::

        DatadogSampler(rules=[
            SamplingRule(sample_rate=1.0, service="my-svc"),
            SamplingRule(sample_rate=0.0, service="less-important"),
            SamplingRule(sample_rate=0.5), # sample all remaining services at 50%
        ])
    """

    __slots__ = (
        "limiter",
        "rules",
        "_rate_limit_always_on",
        "_by_service_samplers",
    )
    _default_key = "service:,env:"

    def __init__(
        self,
        rules: Optional[List[SamplingRule]] = None,
        rate_limit: Optional[int] = None,
        rate_limit_window: float = 1e9,
        rate_limit_always_on: bool = False,
    ):
        """
        Constructor for DatadogSampler sampler

        :param rules: List of :class:`SamplingRule` rules to apply to the root span of every trace, default no rules
        :param default_sample_rate: The default sample rate to apply if no rules matched
        :param rate_limit: Global rate limit (traces per second) to apply to all traces regardless of the rules
            applied to them, (default: ``100``)
        """
        # Set sampling rules
        global_sampling_rules = config._trace_sampling_rules
        if rules is None and global_sampling_rules:
            self.set_sampling_rules(global_sampling_rules)
        else:
            self.rules: List[SamplingRule] = rules or []
        # Set Agent based samplers
        self._by_service_samplers: Dict[str, RateSampler] = {}
        # Set rate limiter
        self._rate_limit_always_on: bool = rate_limit_always_on
        if rate_limit is None:
            rate_limit = int(config._trace_rate_limit)
        self.limiter: RateLimiter = RateLimiter(rate_limit, rate_limit_window)

        log.debug("initialized %r", self)

    @staticmethod
    def _key(service: Optional[str], env: Optional[str]):
        """Compute a key with the same format used by the Datadog agent API."""
        return f"service:{service or ''},env:{env or ''}"

    def update_rate_by_service_sample_rates(self, rate_by_service: Dict[str, float]) -> None:
        samplers: Dict[str, RateSampler] = {}
        for key, sample_rate in rate_by_service.items():
            samplers[key] = RateSampler(sample_rate)
        self._by_service_samplers = samplers

    def __str__(self):
        rates = {key: sampler.sample_rate for key, sampler in self._by_service_samplers.items()}
        return "{}(agent_rates={!r}, limiter={!r}, rules={!r}), rate_limit_always_on={!r}".format(
            self.__class__.__name__,
            rates,
            self.limiter,
            self.rules,
            self._rate_limit_always_on,
        )

    __repr__ = __str__

    def set_sampling_rules(self, rules: str) -> None:
        """Sets the trace sampling rules from a JSON string"""
        if not rules:
            self.rules = []
            return

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
            try:
                sampling_rule = SamplingRule(
                    sample_rate=float(rule["sample_rate"]),
                    service=rule.get("service", SamplingRule.NO_RULE),
                    name=rule.get("name", SamplingRule.NO_RULE),
                    resource=rule.get("resource", SamplingRule.NO_RULE),
                    tags=rule.get("tags", SamplingRule.NO_RULE),
                    provenance=rule.get("provenance", "default"),
                )
                sampling_rules.append(sampling_rule)
            except ValueError as e:
                if config._raise:
                    raise ValueError("Error creating sampling rule {}: {}".format(json.dumps(rule), e))

        # Sort the sampling_rules list using a lambda function as the key
        self.rules = sorted(sampling_rules, key=lambda rule: PROVENANCE_ORDER.index(rule.provenance))

    def sample(self, span: Span) -> bool:
        span._update_tags_from_context()
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

    def _get_sampling_mechanism(self, matched_rule: Optional[SamplingRule], agent_service_based: bool) -> int:
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
