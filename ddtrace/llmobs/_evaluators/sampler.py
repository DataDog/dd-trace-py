import json
import os
from typing import List
from typing import Optional

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.sampling_rule import SamplingRule


try:
    from json.decoder import JSONDecodeError
except ImportError:
    # handling python 2.X import error
    JSONDecodeError = ValueError  # type: ignore

logger = get_logger(__name__)


class EvaluatorRunnerSamplingRule(SamplingRule):
    def __init__(self, sample_rate: float, evaluator_label: Optional[str] = None, span_name: Optional[str] = None):
        super(EvaluatorRunnerSamplingRule, self).__init__(sample_rate)
        self.evaluator_label = evaluator_label
        self.span_name = span_name

    def matches(self, evaluator_label, span_name):
        for prop, pattern in [(span_name, self.span_name), (evaluator_label, self.evaluator_label)]:
            if pattern != self.NO_RULE and prop != pattern:
                return False
        return True

    def __repr__(self):
        return "EvaluatorRunnerSamplingRule(sample_rate={}, evaluator_label={}, span_name={})".format(
            self.sample_rate, self.evaluator_label, self.span_name
        )

    __str__ = __repr__


class EvaluatorRunnerSampler:
    DEFAULT_SAMPLING_RATE = 1.0
    SAMPLING_RULES_ENV_VAR = "_DD_LLMOBS_EVALUATOR_SAMPLING_RULES"
    DEFAULT_SAMPLING_RULE_ENV_VAR = "_DD_LLMOBS_EVALUATOR_DEFAULT_SAMPLE_RATE"

    def __init__(self):
        self.rules = self.parse_rules()
        self.default_sampling_rule = SamplingRule(
            float(os.getenv(EvaluatorRunnerSampler.DEFAULT_SAMPLING_RULE_ENV_VAR, self.DEFAULT_SAMPLING_RATE))
        )

    def sample(self, evaluator_label, span):
        for rule in self.rules:
            if rule.matches(evaluator_label=evaluator_label, span_name=span.name):
                print("matched")
                return rule.sample(span)
        result = self.default_sampling_rule.sample(span)
        return result

    def parse_rules(self) -> List[EvaluatorRunnerSamplingRule]:
        rules = []
        sampling_rules_str = os.getenv(self.SAMPLING_RULES_ENV_VAR)

        def parsing_failed_because(msg, maybe_throw_this):
            if config._raise:
                raise maybe_throw_this(msg)
            logger.warning(msg, exc_info=True)

        if not sampling_rules_str:
            return []
        try:
            json_rules = json.loads(sampling_rules_str)
        except JSONDecodeError:
            reason = "Failed to parse evaluator sampling rules of: `{}`".format(sampling_rules_str)
            parsing_failed_because(reason, ValueError)
            return []

        if not isinstance(json_rules, list):
            reason = "Evaluator sampling rules must be a list of dictionaries"
            parsing_failed_because(reason, ValueError)
            return []

        for rule in json_rules:
            if "sample_rate" not in rule:
                reason = "No sample_rate provided for sampling rule: {}".format(json.dumps(rule))
                parsing_failed_because(reason, KeyError)
                continue
            sample_rate = float(rule["sample_rate"])
            span_name = rule.get("name", SamplingRule.NO_RULE)
            evaluator_label = rule.get("evaluator_label", SamplingRule.NO_RULE)
            rules.append(EvaluatorRunnerSamplingRule(sample_rate, evaluator_label, span_name))
        return rules
