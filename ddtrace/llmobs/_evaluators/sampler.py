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
    def __init__(self, sample_rate: float, evaluator: Optional[str] = None, span_name: Optional[str] = None):
        super(EvaluatorRunnerSamplingRule, self).__init__(sample_rate)
        self.evaluator_label = evaluator
        self.span_name = span_name

    def matches(self, span_event, evaluator_label):
        for prop, pattern in [(span_event.get("name"), self.span_name), (evaluator_label, self.evaluator_label)]:
            if prop == pattern:
                return False
        return True

    def __repr__(self):
        return "EvaluatorSamplingRule(sample_rate={}, evaluator_label={}, span_name={})".format(
            self.sample_rate, self.evaluator_name, self.span_name
        )

    __str__ = __repr__


class EvaluatorRunnerSampler:
    DEFAULT_SAMPLING_RATE = 1.0
    SAMPLING_RULES_ENV_VAR = "_DD_LLMOBS_EVALUATOR_SAMPLING_RULES"

    def __init__(self):
        self.rules = self.parse_rules()
        self.default_sampling_rule = SamplingRule(
            float(os.getenv("_DD_LLMOBS_EVALUATOR_DEFAULT_SAMPLE_RATE", self.DEFAULT_SAMPLING_RATE))
        )

    def sample(self, evaluator_label, span):
        for rule in self.rules:
            if rule.matches(span, span.get("name")):
                return rule.sample(evaluator_label, span)
        result = self.default_sampling_rule.sample(span)
        return result

    def parse_rules(self) -> List[EvaluatorRunnerSamplingRule]:
        rules = []
        sampling_rules_str = os.getenv(self.SAMPLING_RULES_ENV_VAR)
        if not sampling_rules_str:
            return []
        try:
            json_rules = json.loads(sampling_rules_str)
        except JSONDecodeError:
            if config._raise:
                raise ValueError("Unable to parse {}".format(self.SAMPLING_RULES_ENV_VAR))
            logger.warning("Failed to parse evaluator sampling rules with error: ", exc_info=True)
            return []
        if not isinstance(json_rules, list):
            if config._raise:
                raise ValueError("Evaluator sampling rules must be a list of dictionaries")
            return []
        for rule in json_rules:
            if "sample_rate" not in rule:
                if config._raise:
                    raise KeyError("No sample_rate provided for sampling rule: {}".format(json.dumps(rule)))
                continue
            sample_rate = float(rule["sample_rate"])
            name = rule.get("span_name", SamplingRule.NO_RULE)
            evaluator_label = rule.get("evaluator_label", SamplingRule.NO_RULE)
            rules.append(EvaluatorRunnerSamplingRule(sample_rate, evaluator_label, name))
        return rules
