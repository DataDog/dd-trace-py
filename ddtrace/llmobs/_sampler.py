"""Tag-based head sampling for LLM Observability traces.

Rules are configured through ``DD_LLMOBS_SAMPLING_RULES``, which mirrors the shape of
``DD_TRACE_SAMPLING_RULES``::

    DD_LLMOBS_SAMPLING_RULES='[{"tags": {"env": "prod"}, "sample_rate": 0.5},
                              {"tags": {"env": "staging"}, "sample_rate": 0.1}]'

Rules are evaluated in the order they are declared and the first one that matches wins. When no
rule matches, the global ``DD_LLMOBS_SAMPLE_RATE`` is applied.

Timing is the hard part. Tags land on the root span over its whole lifetime, but the decision must
exist before anything leaves the process.

The resolver below decides the sampling decision as late as it safely can and then freezes it on
the trace's root span:

* At root start, only a floor is stamped -- the global rate, since no tag exists yet to match a
  rule on. This guarantees no span can ever ship without a decision.
* The rule-aware decision is resolved the first time it is genuinely needed: an outbound
  injection, a partial flush, or the root finishing. Whichever comes first wins and overwrites
  the floor for every span of the trace.
* From then on it never changes, which is what keeps a trace from being split across two
  decisions. A tag set after that point cannot affect sampling.
"""

import json
from json.decoder import JSONDecodeError
from typing import Any
from typing import Callable
from typing import Optional
from typing import Protocol

from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.constants import SAMPLING_HASH_MODULO
from ddtrace.internal.constants import SAMPLING_KNUTH_FACTOR
from ddtrace.internal.glob_matching import GlobMatcher
from ddtrace.internal.logger import get_logger
from ddtrace.internal.sampling import format_rate
from ddtrace.internal.threads import RLock
from ddtrace.llmobs._constants import LLMOBS_ROOT_SPAN
from ddtrace.llmobs._constants import LLMOBS_SAMPLING
from ddtrace.llmobs._constants import LLMObsSamplingDecision


log = get_logger(__name__)


class _Sampleable(Protocol):
    """What the sampler needs of a span: a trace id to hash.

    Declared here rather than importing ``Span`` so this module does not depend on the tracing
    product (see the ``dependency-direction-analysis`` skill).
    """

    _trace_id_64bits: int


class LLMObsSamplingRule:
    """A single ``DD_LLMOBS_SAMPLING_RULES`` entry.

    Tag values are glob-matched, case-insensitively, with ``*`` meaning any number of characters
    and ``?`` meaning exactly one. Every declared tag must match for the rule to match, so a rule
    declaring no tags matches every trace and is useful as a trailing catch-all.
    """

    __slots__ = ("_sample_rate", "_sampling_id_threshold", "tags")

    def __init__(self, sample_rate: float, tags: Optional[dict[str, object]] = None) -> None:
        """
        :param sample_rate: The rate to sample matching traces at, clamped to [0.0, 1.0].
        :param tags: Mapping of tag name to a glob pattern matched against that tag's value on the
            root span of the LLMObs trace.
        """
        self.sample_rate = sample_rate
        self.tags = {k: GlobMatcher(str(v)) for k, v in tags.items()} if tags else {}

    @property
    def sample_rate(self) -> float:
        return self._sample_rate

    @sample_rate.setter
    def sample_rate(self, sample_rate: float) -> None:
        self._sample_rate = min(1.0, max(0.0, float(sample_rate)))
        self._sampling_id_threshold = self._sample_rate * MAX_UINT_64BITS

    def matches(self, tags: dict[str, str]) -> bool:
        """Return whether this rule applies to a root span carrying the given tags."""
        for tag_key, matcher in self.tags.items():
            if tag_key not in tags:
                return False
            if not matcher.match(str(tags[tag_key])):
                return False
        return True

    def sample(self, span: _Sampleable) -> bool:
        """Return whether ``span`` is kept, using the same deterministic hash as APM sampling."""
        if self._sample_rate == 1.0:
            return True
        if self._sample_rate == 0.0:
            return False
        return ((span._trace_id_64bits * SAMPLING_KNUTH_FACTOR) % SAMPLING_HASH_MODULO) <= self._sampling_id_threshold

    def __repr__(self) -> str:
        return f"LLMObsSamplingRule(sample_rate={self.sample_rate}, tags={self.tags})"


class LLMObsSampler:
    """Applies ``DD_LLMOBS_SAMPLING_RULES``, falling back to a global sample rate."""

    __slots__ = ("_default_rule", "rules")

    def __init__(self, sample_rate: float, rules: Optional[str] = None) -> None:
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

    def match(self, tags: dict[str, str]) -> Optional[LLMObsSamplingRule]:
        """Return the first rule matching the given root-span tags, or ``None``."""
        for rule in self.rules:
            if rule.matches(tags):
                return rule
        return None

    def sample(self, span: _Sampleable, tags: Optional[dict[str, str]] = None) -> tuple[bool, str]:
        """Make the sampling decision for the root span of an LLMObs trace.

        :returns: A ``(sampled, sample_rate)`` pair, where ``sample_rate`` is the formatted rate of
            the rule that produced the decision. It is stamped on every span of the trace so the
            whole trace reports the rate it was actually sampled at.
        """
        rule = self.match(tags) if (self.rules and tags) else None
        if rule is None:
            rule = self._default_rule
        sampled = rule.sample(span)
        log.debug("LLMObs sampling decision for %s: sampled=%s matched_rule=%s", span, sampled, rule)
        return sampled, format_rate(rule.sample_rate)

    def __repr__(self) -> str:
        return f"LLMObsSampler(sample_rate={self.sample_rate}, rules={self.rules!r})"


class LLMObsSamplingResolver:
    """Resolves each LLMObs trace's decision once, storing it on the trace's root span.

    The frozen decision lives in a ctx item on the root rather than in its meta_struct, because
    a partial flush scrubs the meta_struct while later chunks of the same trace still need to
    read the decision. Ctx items are never serialized and are never scrubbed.
    """

    def __init__(self, sampler: LLMObsSampler, tags_getter: Callable[[Any], Optional[dict[str, str]]]) -> None:
        self._sampler = sampler
        self._tags_getter = tags_getter
        self._lock = RLock()

    @staticmethod
    def _as_decision(sampled: bool) -> str:
        return LLMObsSamplingDecision.SAMPLED.value if sampled else LLMObsSamplingDecision.DROPPED.value

    def default_decision(self, root: Any) -> tuple[str, str]:
        """The global-rate decision, stamped at root start.

        Maintains the invariant that every span must carry some decision, so this stands in until
        ``resolve`` can overwrite it with a rule-aware one.
        """
        sampled, sample_rate = self._sampler.sample(root)
        return sample_rate, self._as_decision(sampled)

    def resolve(self, span: Any) -> tuple[Optional[str], Optional[str]]:
        """Return this trace's frozen ``(sample_rate, sampling_decision)``, computing it once.

        Returns ``(None, None)`` for a span with no local root -- a trace continued from another
        process, whose decision was frozen there and inherited. Callers fall back accordingly.
        """
        root = span._get_ctx_item(LLMOBS_ROOT_SPAN)
        if root is None:
            return None, None
        with self._lock:
            frozen: Optional[tuple[str, str]] = root._get_ctx_item(LLMOBS_SAMPLING)
            if frozen is not None:
                return frozen
            sampled, sample_rate = self._sampler.sample(root, self._tags_getter(root) or {})
            frozen = (sample_rate, self._as_decision(sampled))
            root._set_ctx_item(LLMOBS_SAMPLING, frozen)
            log.debug("LLMObs sampling resolved for %s: %s", root, frozen)
            return frozen
