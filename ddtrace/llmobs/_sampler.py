"""Tag-based head sampling for LLM Observability traces.

Rules are configured through ``DD_LLMOBS_SAMPLING_RULES``, which mirrors the shape of
``DD_TRACE_SAMPLING_RULES``::

    DD_LLMOBS_SAMPLING_RULES='[{"tags": {"env": "prod"}, "sample_rate": 0.5},
                              {"tags": {"env": "staging"}, "sample_rate": 0.1}]'

Rules are evaluated in the order they are declared and the first one that matches wins. When no
rule matches, the global ``DD_LLMOBS_SAMPLE_RATE`` is applied.

Timing is the hard part. Tags land on the root span over its whole lifetime, but the decision must
exist before anything leaves the process, and a shipped span event cannot be retracted -- so a
decision revised after something shipped would leave one trace carrying two different answers.

The registry below decides as late as it safely can and then freezes:

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
from typing import Optional
import weakref

from ddtrace._trace.span import Span
from ddtrace.internal.constants import MAX_UINT_64BITS
from ddtrace.internal.constants import SAMPLING_HASH_MODULO
from ddtrace.internal.constants import SAMPLING_KNUTH_FACTOR
from ddtrace.internal.glob_matching import GlobMatcher
from ddtrace.internal.logger import get_logger
from ddtrace.internal.sampling import format_rate
from ddtrace.internal.threads import RLock
from ddtrace.llmobs._constants import LLMObsSamplingDecision
from ddtrace.llmobs._utils import get_llmobs_tags


log = get_logger(__name__)

DEFAULT_SAMPLE_RATE = 1.0

# Ceiling on concurrently in-flight LLMObs traces awaiting a sampling decision. Entries are retired
# when a trace completes, so this is only reached if roots are abandoned without finishing. Past the
# cap we stop registering, and those traces keep the floor stamped at root start.
MAX_PENDING_DECISIONS = 4096


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

    def sample(self, span: Span) -> bool:
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

    def match(self, tags: dict[str, str]) -> Optional[LLMObsSamplingRule]:
        """Return the first rule matching the given root-span tags, or ``None``."""
        for rule in self.rules:
            if rule.matches(tags):
                return rule
        return None

    def sample(self, span: Span, tags: Optional[dict[str, str]] = None) -> tuple[bool, str]:
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


class _PendingDecision:
    """One in-flight LLMObs trace's sampling decision: unresolved, then frozen."""

    __slots__ = ("_root", "sample_rate", "sampling_decision")

    def __init__(self, root: Span) -> None:
        # Weak, so an abandoned root cannot keep its span graph alive through this registry.
        self._root = weakref.ref(root)
        self.sample_rate: Optional[str] = None
        self.sampling_decision: Optional[str] = None

    @property
    def root(self) -> Optional[Span]:
        return self._root()

    @property
    def resolved(self) -> bool:
        return self.sampling_decision is not None


class LLMObsSamplingRegistry:
    """Tracks in-flight LLMObs traces and resolves each one's decision exactly once.

    Lives here rather than on ``LLMObs`` so ``_processor`` can depend on it without importing
    ``_llmobs`` (which imports ``_processor``).
    """

    def __init__(self, sampler: LLMObsSampler) -> None:
        self._sampler = sampler
        self._pending: dict[str, _PendingDecision] = {}
        # Plain dict access under the GIL is atomic, but resolve() is read-modify-write and two
        # threads injecting at once must not produce two different decisions for one trace.
        self._lock = RLock()

    @staticmethod
    def _as_decision(sampled: bool) -> str:
        return LLMObsSamplingDecision.SAMPLED.value if sampled else LLMObsSamplingDecision.DROPPED.value

    def default_decision(self, root: Span) -> tuple[str, str]:
        """The global-rate decision, stamped at root start as a floor.

        Rules can't be evaluated this early — the root has no tags yet — but every span must carry
        some decision, so this stands in until ``resolve`` can overwrite it with a rule-aware one.
        It is also what a trace falls back to when the registry cannot answer for it at all.
        """
        sampled, sample_rate = self._sampler.sample(root)
        return sample_rate, self._as_decision(sampled)

    def register_root(self, llmobs_trace_id: str, root: Span) -> None:
        """Note that an LLMObs trace has started, without deciding anything yet."""
        with self._lock:
            if len(self._pending) >= MAX_PENDING_DECISIONS:
                log.debug(
                    "LLMObs sampling registry is full (%d entries); trace %s keeps the global sample rate.",
                    MAX_PENDING_DECISIONS,
                    llmobs_trace_id,
                )
                return
            self._pending[llmobs_trace_id] = _PendingDecision(root)

    def resolve(self, llmobs_trace_id: Optional[str]) -> tuple[Optional[str], Optional[str]]:
        """Return this trace's ``(sample_rate, sampling_decision)``, computing it on first call.

        Returns ``(None, None)`` when the trace is unknown here — a trace continued from another
        process, or one dropped past the registry cap. Callers fall back accordingly.
        """
        if llmobs_trace_id is None:
            return None, None
        with self._lock:
            pending = self._pending.get(llmobs_trace_id)
            if pending is None:
                return None, None
            if pending.resolved:
                return pending.sample_rate, pending.sampling_decision
            root = pending.root
            if root is None:
                # Root was garbage collected before anything needed the decision.
                return None, None
            sampled, sample_rate = self._sampler.sample(root, get_llmobs_tags(root) or {})
            pending.sample_rate = sample_rate
            pending.sampling_decision = self._as_decision(sampled)
            return pending.sample_rate, pending.sampling_decision

    def discard(self, llmobs_trace_id: str) -> None:
        """Forget a trace once its spans have been stamped and routed."""
        with self._lock:
            self._pending.pop(llmobs_trace_id, None)

    def clear(self) -> None:
        with self._lock:
            self._pending.clear()
