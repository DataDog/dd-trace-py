import json
import os
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.sampling_rule import SamplingRule


logger = get_logger(__name__)


class EvaluatorSamplingRule(SamplingRule):
    def __init__(self, sample_rate: float, evaluator: Optional[str] = None, span_name: Optional[str] = None):
        super(EvaluatorSamplingRule, self).__init__(sample_rate)
        self.evaluator_label = self.choose_matcher(evaluator)
        self.span_name = self.choose_matcher(span_name)

    def matches(self, span_event, evaluator_label):
        for prop, pattern in [(span_event.get("name"), self.span_name), (evaluator_label, self.evaluator_label)]:
            if prop == pattern:
                return False
        return True

    def __call__(self, span):
        if self.span_name == span.get("name"):
            return True
        return False

    def __repr__(self):
        return "EvaluatorSamplingRule(sample_rate={}, evaluator_label={}, span_name={})".format(
            self.sample_rate, self.evaluator_name, self.span_name
        )


class EvaluatorSampler:
    def __init__(self):
        self.sampling_rules = []
        self.sampling_rules = self.parse_rules()
        self.default_sampling_rule = SamplingRule(float(os.getenv("_DD_LLMOBS_EVALUATOR_DEFAULT_SAMPLE_RATE", 1)))

    def sample(self, evaluator_label, span):
        for rule in self.sampling_rules:
            if rule.matches(span, span.get("name")):
                return rule.sample(evaluator_label, span)
        result = self.default_sampling_rule.sample(span)
        return result

    def parse_rules(self) -> List[SamplingRule]:
        sampling_rules_str = os.getenv("_DD_LLMOBS_EVALUATOR_SAMPLING_RULES")
        if sampling_rules_str is not None:
            try:
                sampling_rules = json.loads(sampling_rules_str)
                if not isinstance(sampling_rules, list):
                    raise TypeError("Sampling rules must be a list of dictionaries")
                for rule in sampling_rules:
                    if not isinstance(rule, dict):
                        raise TypeError("Sampling rules must be a list of dictionaries")
                    sample_rate = rule.get("sample_rate")
                    if not sample_rate:
                        raise TypeError("Sampling rules must have a sample rate")

                    evaluator = rule.get("evaluator")
                    if rule.get("evaluator") is not None and not isinstance(evaluator, str):
                        raise TypeError("'evaluator' key in sampling rule must have string value")

                    span_name = rule.get("name")
                    if span_name is not None and not isinstance(span_name, str):
                        raise TypeError("'name' key in sampling rule must have string value")

                    self.sampling_rules.append(EvaluatorSamplingRule(sample_rate, evaluator, span_name))
            except TypeError:
                logger.error("Failed to parse sampling rules with error: ", exc_info=True)
        return []
