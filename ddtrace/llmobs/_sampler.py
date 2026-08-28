"""Env-based head sampling for LLM Observability traces.

The sampling decision is made once, on the root LLMObs span of a trace, and is then inherited by
every child span (and propagated across distributed boundaries) by ``LLMObs._activate_llmobs_span``.

Rules are configured through ``DD_LLMOBS_SAMPLING_RULES``, which mirrors the shape of
``DD_TRACE_SAMPLING_RULES``::

    DD_LLMOBS_SAMPLING_RULES='[{"env": "prod", "sample_rate": 0.5},
                              {"env": "staging", "sample_rate": 0.1}]'

Rules are evaluated in the order they are declared and the first one that matches wins. When no
rule matches, the global ``DD_LLMOBS_SAMPLE_RATE`` is applied.
"""

import json
from json.decoder import JSONDecodeError
from typing import Optional

from ddtrace._trace.span import Span
from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.constants import SAMPLING_HASH_MODULO
from ddtrace.internal.constants import SAMPLING_KNUTH_FACTOR
from ddtrace.internal.glob_matching import GlobMatcher
from ddtrace.internal.logger import get_logger
from ddtrace.internal.sampling import format_rate


log = get_logger(__name__)

DEFAULT_SAMPLE_RATE = 1.0


class LLMObsSamplingRule:
    """A single ``DD_LLMOBS_SAMPLING_RULES`` entry.

    The env is glob-matched, case-insensitively, with ``*`` meaning any number of characters and
    ``?`` meaning exactly one character. A rule that declares no env matches every trace.
    """

    __slots__ = ("_sample_rate", "_sampling_id_threshold", "env")

    def __init__(self, sample_rate: float, env: Optional[str] = None) -> None:
        """
        :param sample_rate: The rate to sample matching traces at, clamped to [0.0, 1.0].
        :param env: Glob pattern matched against the env the traced service is running under.
        """
        self.sample_rate = sample_rate
        self.env = GlobMatcher(env) if env is not None else None

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, sample_rate: float) -> None:
        self._sample_rate = min(1.0, max(0.0, float(sample_rate)))
        self._sampling_id_threshold = self._sample_rate * MAX_UINT_64BITS

    def matches(self, env: str) -> bool:
        """Return whether this rule applies to a trace running under the given env."""
        return self.env is None or self.env.match(env)

    def sample(self, span: Span) -> bool:
        """Return whether ``span`` is kept, using the same deterministic hash as APM sampling."""
        if self._sample_rate == 1.0:
            return True
        if self._sample_rate == 0.0:
            return False
        return ((span._trace_id_64bits * SAMPLING_KNUTH_FACTOR) % SAMPLING_HASH_MODULO) <= self._sampling_id_threshold

    def __repr__(self) -> str:
        return f"LLMObsSamplingRule(sample_rate={self.sample_rate}, env={self.env})"


class LLMObsSampler:
    """Applies ``DD_LLMOBS_SAMPLING_RULES``, falling back to a global sample rate."""

    __slots__ = ("_default_rule", "rules")

    def __init__(self, sample_rate: float = DEFAULT_SAMPLE_RATE, rules: Optional[str] = None) -> None:
        """
        :param sample_rate: The global rate applied when no rule matches (``DD_LLMOBS_SAMPLE_RATE``).
        :param rules: The raw JSON value of ``DD_LLMOBS_SAMPLING_RULES``.
        """
        self._default_rule = LLMObsSamplingRule(sample_rate=sample_rate)
        self.rules: list[LLMObsSamplingRule] = self._parse_rules(rules) if rules else []

    @property
    def sample_rate(self) -> float:
        """The global sample rate applied to traces that match no rule."""
        return self._default_rule.sample_rate

    def set_sample_rate(self, sample_rate: float) -> None:
        self._default_rule.sample_rate = sample_rate

    @staticmethod
    def _parse_rules(rules: str) -> list[LLMObsSamplingRule]:
        parsed: list[LLMObsSamplingRule] = []
        try:
            json_rules = json.loads(rules)
        except JSONDecodeError:
            log.warning("Failed to parse DD_LLMOBS_SAMPLING_RULES=%r as JSON. Ignoring all rules.", rules)
            return []
        if not isinstance(json_rules, list):
            log.warning("DD_LLMOBS_SAMPLING_RULES must be a JSON list, got %r. Ignoring all rules.", rules)
            return []
        for rule in json_rules:
            if not isinstance(rule, dict):
                log.warning("Skipping LLMObs sampling rule %r: rules must be JSON objects.", rule)
                continue
            if "sample_rate" not in rule:
                log.warning("Skipping LLMObs sampling rule %r: no sample_rate provided.", rule)
                continue
            try:
                parsed.append(LLMObsSamplingRule(**rule))
            except (TypeError, ValueError):
                log.warning("Skipping invalid LLMObs sampling rule %r.", rule, exc_info=True)
        return parsed

    def match(self, env: str) -> Optional[LLMObsSamplingRule]:
        """Return the first rule matching the given env, or ``None``."""
        for rule in self.rules:
            if rule.matches(env):
                return rule
        return None

    def sample(self, span: Span, env: Optional[str] = None) -> tuple[bool, str]:
        """Make the head sampling decision for a root LLMObs span.

        :returns: A ``(sampled, sample_rate)`` pair, where ``sample_rate`` is the formatted rate of
            the rule that produced the decision. It is propagated to child spans so the whole trace
            reports the rate it was actually sampled at.
        """
        rule = self.match(env or "") if self.rules else None
        if rule is None:
            rule = self._default_rule
        sampled = rule.sample(span)
        log.debug("LLMObs sampling decision for %s: sampled=%s matched_rule=%s", span, sampled, rule)
        return sampled, format_rate(rule.sample_rate)

    def __repr__(self) -> str:
        return f"LLMObsSampler(sample_rate={self.sample_rate}, rules={self.rules!r})"
