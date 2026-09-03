import mock
import pytest

from ddtrace._trace.span import Span
from ddtrace.llmobs._constants import LLMOBS_SAMPLING
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._sampler import LLMObsSampler
from ddtrace.llmobs._sampler import LLMObsSamplingResolver
from ddtrace.llmobs._sampler import LLMObsSamplingRule
from ddtrace.llmobs._utils import get_llmobs_tags


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
        """The motivating case: sample prod at 50% and staging at 10%.

        Asserts which rule each env selects and the rate it reports, not the resulting
        distribution -- the keep/drop maths is APM's and is covered by APM's own tests.
        """
        sampler = LLMObsSampler(
            sample_rate=1.0,
            rules='[{"tags": {"env": "prod"}, "sample_rate": 0.5}, {"tags": {"env": "staging"}, "sample_rate": 0.1}]',
        )
        assert sampler.match({"env": "prod"}).sample_rate == 0.5
        assert sampler.match({"env": "staging"}).sample_rate == 0.1
        assert sampler.match({"env": "dev"}) is None
        # An env matching no rule falls through to the global rate.
        assert sampler.sample(_span(), {"env": "dev"}) == (True, "1")


class TestLLMObsSamplingResolver:
    def _resolver(self, sample_rate=1.0, rules=None):
        return LLMObsSamplingResolver(LLMObsSampler(sample_rate=sample_rate, rules=rules), get_llmobs_tags)

    @staticmethod
    def _rooted(resolver, span, share_with=None):
        """Give a span the sampling state that _activate_llmobs_span would set.

        Passing share_with reuses another span's state, as a child inherits its parent's.
        """
        if share_with is not None:
            span._set_ctx_item(LLMOBS_SAMPLING, share_with._get_ctx_item(LLMOBS_SAMPLING))
        else:
            state, _, _ = resolver.start_trace(span)
            span._set_ctx_item(LLMOBS_SAMPLING, state)
        return span

    def test_span_without_a_local_root_resolves_to_nothing(self):
        """A trace continued from another process inherits its decision instead."""
        assert self._resolver().resolve(_span(tags={})) == (None, None)

    def test_start_trace_floor_ignores_rules(self):
        """The floor is the global rate: at root start there are no tags to match a rule on."""
        resolver = self._resolver(sample_rate=1.0, rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')
        assert resolver.start_trace(_span(tags={"tier": "gold"}))[1:] == ("1", "1")

    def test_start_trace_floor_follows_the_global_rate(self):
        assert self._resolver(sample_rate=0.0).start_trace(_span())[1:] == ("0", "0")
        assert self._resolver(sample_rate=1.0).start_trace(_span())[1:] == ("1", "1")

    def test_resolve_uses_tags_present_at_resolve_time(self):
        """The whole point: tags set after the root started still affect the decision."""
        resolver = self._resolver(rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')
        root = self._rooted(resolver, _span(tags={}))
        # Tag lands after activation, as LLMObs.annotate() would do.
        root._get_struct_tag(LLMOBS_STRUCT.KEY)[LLMOBS_STRUCT.TAGS]["tier"] = "gold"
        assert resolver.resolve(root) == ("0", "0")

    def test_decision_is_frozen_after_first_resolve(self):
        resolver = self._resolver(rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')
        root = self._rooted(resolver, _span(tags={}))
        first = resolver.resolve(root)
        assert first == ("1", "1")  # no tag yet, so the global rate applied
        root._get_struct_tag(LLMOBS_STRUCT.KEY)[LLMOBS_STRUCT.TAGS]["tier"] = "gold"
        assert resolver.resolve(root) == first

    def test_child_resolves_through_its_root_pointer(self):
        resolver = self._resolver(rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')
        root = self._rooted(resolver, _span(tags={"tier": "gold"}))
        child = self._rooted(resolver, _span(tags={}), share_with=root)
        assert resolver.resolve(child) == ("0", "0")

    def test_frozen_decision_survives_the_meta_struct_scrub(self):
        """A partial flush scrubs the root's meta_struct while later chunks still need the
        decision, which is why the frozen value lives in a ctx item.
        """
        resolver = self._resolver(rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')
        root = self._rooted(resolver, _span(tags={"tier": "gold"}))
        child = self._rooted(resolver, _span(tags={}), share_with=root)
        assert resolver.resolve(root) == ("0", "0")
        root._remove_struct_tag(LLMOBS_STRUCT.KEY)  # what LLMObsProcessor._scrub does
        assert resolver.resolve(child) == ("0", "0")

    def test_traces_are_independent(self):
        resolver = self._resolver(rules='[{"tags": {"tier": "gold"}, "sample_rate": 0}]')
        gold = self._rooted(resolver, _span(tags={"tier": "gold"}))
        free = self._rooted(resolver, _span(tags={"tier": "free"}))
        assert resolver.resolve(gold) == ("0", "0")
        assert resolver.resolve(free) == ("1", "1")
