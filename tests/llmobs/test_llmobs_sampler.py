import mock
import pytest

from ddtrace._trace.span import Span
from ddtrace.llmobs._sampler import LLMObsSampler
from ddtrace.llmobs._sampler import LLMObsSamplingRule


def _span(trace_id=None):
    return Span("root", trace_id=trace_id)


class TestLLMObsSamplingRule:
    def test_sample_rate_is_clamped(self):
        assert LLMObsSamplingRule(sample_rate=1.5).sample_rate == 1.0
        assert LLMObsSamplingRule(sample_rate=-0.5).sample_rate == 0.0
        assert LLMObsSamplingRule(sample_rate="0.25").sample_rate == 0.25

    def test_rule_without_env_matches_everything(self):
        rule = LLMObsSamplingRule(sample_rate=0.5)
        assert rule.matches("")
        assert rule.matches("prod")

    @pytest.mark.parametrize(
        "rule_env,span_env,expected",
        [
            ("prod", "prod", True),
            ("prod", "staging", False),
            ("prod", "", False),
            ("PROD", "prod", True),  # glob matching is case insensitive
            ("prod", "PROD", True),
            ("prod*", "prod-eu", True),
            ("prod-?", "prod-1", True),
            ("prod-?", "prod-eu", False),
            ("*", "anything", True),
            ("*", "", True),
            ("", "", True),
            ("", "prod", False),
        ],
    )
    def test_env_matching(self, rule_env, span_env, expected):
        assert LLMObsSamplingRule(sample_rate=1.0, env=rule_env).matches(span_env) is expected

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
        sampler = LLMObsSampler(rules='[{"env": "prod", "sample_rate": 0.5}, {"env": "staging", "sample_rate": 0.1}]')
        assert [rule.sample_rate for rule in sampler.rules] == [0.5, 0.1]

    @pytest.mark.parametrize(
        "rules",
        [
            "not json",
            '{"sample_rate": 0.5}',  # a JSON object rather than a list
            "[",
        ],
    )
    def test_unparseable_rules_are_ignored(self, rules):
        with mock.patch("ddtrace.llmobs._sampler.log") as mock_log:
            sampler = LLMObsSampler(sample_rate=0.5, rules=rules)
        assert sampler.rules == []
        assert sampler.sample_rate == 0.5
        assert mock_log.warning.called

    @pytest.mark.parametrize(
        "rule",
        [
            '{"env": "prod"}',  # no sample_rate
            '{"sample_rate": 0.5, "unknown_field": "x"}',
            '{"sample_rate": 0.5, "tags": {"env": "prod"}}',  # tag matchers are not supported yet
            '"a string"',
            "null",
        ],
    )
    def test_invalid_rule_is_skipped_and_others_kept(self, rule):
        with mock.patch("ddtrace.llmobs._sampler.log") as mock_log:
            sampler = LLMObsSampler(rules=f'[{rule}, {{"env": "dev", "sample_rate": 0.2}}]')
        assert [rule.sample_rate for rule in sampler.rules] == [0.2]
        assert mock_log.warning.called


class TestLLMObsSamplerSampling:
    def test_first_matching_rule_wins(self):
        sampler = LLMObsSampler(rules='[{"env": "prod", "sample_rate": 0}, {"env": "prod", "sample_rate": 1}]')
        assert sampler.sample(_span(), "prod") == (False, "0")

    def test_rule_rate_is_reported(self):
        sampler = LLMObsSampler(rules='[{"env": "prod", "sample_rate": 0.25}]')
        _, sample_rate = sampler.sample(_span(), "prod")
        assert sample_rate == "0.25"

    def test_falls_back_to_global_rate_when_no_rule_matches(self):
        sampler = LLMObsSampler(sample_rate=0.0, rules='[{"env": "prod", "sample_rate": 1}]')
        assert sampler.sample(_span(), "staging") == (False, "0")
        assert sampler.sample(_span(), "prod") == (True, "1")

    @pytest.mark.parametrize("env", [None, ""])
    def test_unset_env_does_not_match_an_env_rule(self, env):
        sampler = LLMObsSampler(sample_rate=1.0, rules='[{"env": "prod", "sample_rate": 0}]')
        assert sampler.sample(_span(), env) == (True, "1")

    def test_rule_without_env_acts_as_a_catch_all(self):
        sampler = LLMObsSampler(sample_rate=1.0, rules='[{"env": "prod", "sample_rate": 1}, {"sample_rate": 0}]')
        assert sampler.sample(_span(), "prod") == (True, "1")
        assert sampler.sample(_span(), "staging") == (False, "0")

    def test_env_specific_rates(self):
        """The motivating case: sample prod at 50% and staging at 10%."""
        sampler = LLMObsSampler(
            sample_rate=1.0,
            rules='[{"env": "prod", "sample_rate": 0.5}, {"env": "staging", "sample_rate": 0.1}]',
        )
        rates = {}
        for env in ("prod", "staging", "dev"):
            kept = sum(1 for trace_id in range(1, 2001) if sampler.sample(_span(trace_id=trace_id), env)[0])
            rates[env] = kept / 2000
        assert 0.45 <= rates["prod"] <= 0.55
        assert 0.05 <= rates["staging"] <= 0.15
        assert rates["dev"] == 1.0

    def test_set_sample_rate_updates_fallback(self):
        sampler = LLMObsSampler(sample_rate=1.0)
        sampler.set_sample_rate(0.0)
        assert sampler.sample_rate == 0.0
        assert sampler.sample(_span(), "prod") == (False, "0")
