import mock
import pytest

from ddtrace._trace.span import Span
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._sampler import MAX_PENDING_DECISIONS
from ddtrace.llmobs._sampler import LLMObsSampler
from ddtrace.llmobs._sampler import LLMObsSamplingRegistry
from ddtrace.llmobs._sampler import LLMObsSamplingRule


def _span(trace_id=None, tags=None):
    """A span carrying just enough LLMObs meta_struct for the sampler to read its tags."""
    span = Span("root", trace_id=trace_id)
    if tags is not None:
        span._set_struct_tag(LLMOBS_STRUCT.KEY, {LLMOBS_STRUCT.TAGS: tags})
    return span


class TestLLMObsSamplingRule:
    def test_sample_rate_is_clamped(self):
        assert LLMObsSamplingRule(sample_rate=1.5).sample_rate == 1.0
        assert LLMObsSamplingRule(sample_rate=-0.5).sample_rate == 0.0
        assert LLMObsSamplingRule(sample_rate="0.25").sample_rate == 0.25

    def test_rule_without_tags_matches_everything(self):
        rule = LLMObsSamplingRule(sample_rate=0.5)
        assert rule.matches({})
        assert rule.matches({"env": "prod", "ml_app": "app"})

    @pytest.mark.parametrize(
        "rule_tags,span_tags,expected",
        [
            ({"env": "prod"}, {"env": "prod"}, True),
            ({"env": "prod"}, {"env": "staging"}, False),
            ({"env": "prod"}, {}, False),
            ({"env": "prod"}, {"env": ""}, False),
            ({"env": "PROD"}, {"env": "prod"}, True),  # glob matching is case insensitive
            ({"env": "prod"}, {"env": "PROD"}, True),
            ({"env": "prod*"}, {"env": "prod-eu"}, True),
            ({"env": "prod-?"}, {"env": "prod-1"}, True),
            ({"env": "prod-?"}, {"env": "prod-eu"}, False),
            ({"env": "*"}, {"env": "anything"}, True),
            ({"env": "*"}, {}, False),  # a wildcard still requires the tag to be present
            ({"env": "prod", "team": "ml"}, {"env": "prod", "team": "ml"}, True),
            ({"env": "prod", "team": "ml"}, {"env": "prod", "team": "infra"}, False),
            ({"env": "prod", "team": "ml"}, {"env": "prod"}, False),
            ({"version": 3}, {"version": "3"}, True),  # non-string rule values are stringified
            # ml_app, service and session_id are ordinary members of the LLMObs tagset
            ({"ml_app": "my-app"}, {"ml_app": "my-app", "env": "prod"}, True),
            ({"service": "svc"}, {"service": "other"}, False),
        ],
    )
    def test_tag_matching(self, rule_tags, span_tags, expected):
        assert LLMObsSamplingRule(sample_rate=1.0, tags=rule_tags).matches(span_tags) is expected

    def test_sample_rate_one_and_zero_are_absolute(self):
        span = _span()
        assert LLMObsSamplingRule(sample_rate=1.0).sample(span) is True
        assert LLMObsSamplingRule(sample_rate=0.0).sample(span) is False

    def test_sampling_is_deterministic_per_trace(self):
        rule = LLMObsSamplingRule(sample_rate=0.5)
        span = _span(trace_id=1234567890)
        assert rule.sample(span) == rule.sample(span) == rule.sample(_span(trace_id=1234567890))

    def test_sampling_follows_configured_rate(self):
        """Across 2000 trace IDs the kept fraction should land close to the rule's rate."""
        rule = LLMObsSamplingRule(sample_rate=0.3)
        kept = sum(1 for trace_id in range(1, 2001) if rule.sample(_span(trace_id=trace_id)))
        assert 0.25 <= kept / 2000 <= 0.35


class TestLLMObsSamplerRuleParsing:
    def test_no_rules(self):
        assert LLMObsSampler(sample_rate=0.5).rules == []
        assert LLMObsSampler(sample_rate=0.5, rules="").rules == []

    def test_parses_rules_in_order(self):
        sampler = LLMObsSampler(
            sample_rate=1.0,
            rules='[{"tags": {"env": "prod"}, "sample_rate": 0.5}, {"tags": {"env": "staging"}, "sample_rate": 0.1}]',
        )
        assert [rule.sample_rate for rule in sampler.rules] == [0.5, 0.1]

    @pytest.mark.parametrize("rules", ["not json", '{"sample_rate": 0.5}', "["])
    def test_unparseable_rules_are_ignored(self, rules):
        with mock.patch("ddtrace.llmobs._sampler.log") as mock_log:
            sampler = LLMObsSampler(sample_rate=0.5, rules=rules)
        assert sampler.rules == []
        assert sampler.sample_rate == 0.5
        assert mock_log.warning.called

    @pytest.mark.parametrize(
        "rule",
        [
            '{"tags": {"env": "prod"}}',  # no sample_rate
            '{"sample_rate": 0.5, "unknown_field": "x"}',
            '{"sample_rate": 0.5, "env": "prod"}',  # env is not a top-level matcher
            '"a string"',
            "null",
        ],
    )
    def test_invalid_rule_is_skipped_and_others_kept(self, rule):
        with mock.patch("ddtrace.llmobs._sampler.log") as mock_log:
            sampler = LLMObsSampler(
                sample_rate=1.0, rules=f'[{rule}, {{"tags": {{"env": "dev"}}, "sample_rate": 0.2}}]'
            )
        assert [r.sample_rate for r in sampler.rules] == [0.2]
        assert mock_log.warning.called


class TestLLMObsSamplerSampling:
    def test_first_matching_rule_wins(self):
        sampler = LLMObsSampler(
            sample_rate=1.0,
            rules='[{"tags": {"env": "prod"}, "sample_rate": 0}, {"tags": {"env": "prod"}, "sample_rate": 1}]',
        )
        assert sampler.sample(_span(), {"env": "prod"}) == (False, "0")

    def test_rule_rate_is_reported(self):
        sampler = LLMObsSampler(sample_rate=1.0, rules='[{"tags": {"env": "prod"}, "sample_rate": 0.25}]')
        assert sampler.sample(_span(), {"env": "prod"})[1] == "0.25"

    def test_falls_back_to_global_rate_when_no_rule_matches(self):
        sampler = LLMObsSampler(sample_rate=0.0, rules='[{"tags": {"env": "prod"}, "sample_rate": 1}]')
        assert sampler.sample(_span(), {"env": "staging"}) == (False, "0")
        assert sampler.sample(_span(), {"env": "prod"}) == (True, "1")

    def test_rule_without_tags_acts_as_a_catch_all(self):
        sampler = LLMObsSampler(
            sample_rate=1.0, rules='[{"tags": {"env": "prod"}, "sample_rate": 1}, {"sample_rate": 0}]'
        )
        assert sampler.sample(_span(), {"env": "prod"}) == (True, "1")
        assert sampler.sample(_span(), {"env": "staging"}) == (False, "0")

    def test_env_specific_rates(self):
        """The motivating case: sample prod at 50% and staging at 10%."""
        sampler = LLMObsSampler(
            sample_rate=1.0,
            rules='[{"tags": {"env": "prod"}, "sample_rate": 0.5}, {"tags": {"env": "staging"}, "sample_rate": 0.1}]',
        )
        rates = {}
        for env in ("prod", "staging", "dev"):
            kept = sum(1 for tid in range(1, 2001) if sampler.sample(_span(trace_id=tid), {"env": env})[0])
            rates[env] = kept / 2000
        assert 0.45 <= rates["prod"] <= 0.55
        assert 0.05 <= rates["staging"] <= 0.15
        assert rates["dev"] == 1.0


class TestLLMObsSamplingRegistry:
    def _registry(self, sample_rate=1.0, rules=None):
        return LLMObsSamplingRegistry(LLMObsSampler(sample_rate=sample_rate, rules=rules))

    def test_unknown_trace_resolves_to_nothing(self):
        registry = self._registry()
        assert registry.resolve("deadbeef") == (None, None)
        assert registry.resolve(None) == (None, None)

    def test_default_decision_ignores_rules(self):
        """The floor is the global rate: at root start there are no tags to match a rule on."""
        registry = self._registry(sample_rate=1.0, rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')
        assert registry.default_decision(_span(tags={"tier": "gold"})) == ("1", "1")

    def test_default_decision_follows_the_global_rate(self):
        assert self._registry(sample_rate=0.0).default_decision(_span()) == ("0", "0")
        assert self._registry(sample_rate=1.0).default_decision(_span()) == ("1", "1")

    def test_resolve_uses_tags_present_at_resolve_time(self):
        """The whole point: tags set after the root started still affect the decision."""
        registry = self._registry(rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')
        span = _span(tags={})
        registry.register_root("t1", span)
        # Tag lands after registration, as LLMObs.annotate() would do.
        span._get_struct_tag(LLMOBS_STRUCT.KEY)[LLMOBS_STRUCT.TAGS]["tier"] = "gold"
        assert registry.resolve("t1") == ("0", "0")

    def test_decision_is_frozen_after_first_resolve(self):
        registry = self._registry(rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')
        span = _span(tags={})
        registry.register_root("t1", span)
        first = registry.resolve("t1")
        assert first == ("1", "1")  # no tag yet, so the global rate applied
        # A later tag must not revise a decision that may already have shipped.
        span._get_struct_tag(LLMOBS_STRUCT.KEY)[LLMOBS_STRUCT.TAGS]["tier"] = "gold"
        assert registry.resolve("t1") == first

    def test_discard_forgets_the_trace(self):
        registry = self._registry()
        span = _span(tags={})
        registry.register_root("t1", span)
        assert registry.resolve("t1") != (None, None)
        registry.discard("t1")
        assert registry.resolve("t1") == (None, None)

    def test_clear_forgets_everything(self):
        registry = self._registry()
        span = _span(tags={})
        registry.register_root("t1", span)
        registry.clear()
        assert registry.resolve("t1") == (None, None)

    def test_traces_are_independent(self):
        registry = self._registry(rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')
        gold, free = _span(tags={"tier": "gold"}), _span(tags={"tier": "free"})
        registry.register_root("t1", gold)
        registry.register_root("t2", free)
        assert registry.resolve("t1") == ("0", "0")
        assert registry.resolve("t2") == ("1", "1")

    def test_registry_is_capped(self):
        registry = self._registry()
        spans = [_span(tags={}) for _ in range(MAX_PENDING_DECISIONS + 5)]
        for i, span in enumerate(spans):
            registry.register_root(f"t{i}", span)
        assert registry.resolve(f"t{MAX_PENDING_DECISIONS - 1}") != (None, None)
        # Past the cap nothing is tracked, so those traces fall back rather than growing the dict.
        assert registry.resolve(f"t{MAX_PENDING_DECISIONS + 4}") == (None, None)

    def test_collected_root_resolves_to_nothing(self):
        """An abandoned root must not be kept alive by the registry."""
        import gc

        registry = self._registry()
        span = _span(tags={})
        registry.register_root("t1", span)
        del span
        gc.collect()
        assert registry.resolve("t1") == (None, None)
