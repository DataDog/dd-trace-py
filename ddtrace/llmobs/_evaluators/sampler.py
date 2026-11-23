import json
from json.decoder import JSONDecodeError
from typing import Optional
from typing import Union
from typing import cast

from ddtrace import config
from ddtrace._trace.sampling_rule import SamplingRule
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.settings import env
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.llmobs._span import _LLMObsSpan


logger = get_logger(__name__)


class EvaluatorRunnerSamplingRule(SamplingRule):
    NO_RULE = object()
    SAMPLE_RATE_KEY = "sample_rate"
    EVALUATOR_LABEL_KEY = "evaluator_label"
    SPAN_NAME_KEY = "span_name"

    def __init__(
        self,
        sample_rate: float,
        evaluator_label: Optional[Union[str, object]] = None,
        span_name: Optional[object] = None,
    ) -> None:
        super().__init__(sample_rate)
        self.evaluator_label = evaluator_label
        self.span_name = span_name

    # Intentionally narrows the base SamplingRule.matches(span) interface: evaluator rules match on
    # (evaluator_label, span_name) instead of a span.
    def matches(self, evaluator_label: str, span_name: str) -> bool:  # type: ignore[override]
        for prop, pattern in [(span_name, self.span_name), (evaluator_label, self.evaluator_label)]:
            if pattern != self.NO_RULE and prop != pattern:
                return False
        return True

    def __repr__(self) -> str:
        return (
            f"EvaluatorRunnerSamplingRule(sample_rate={self.sample_rate},"
            f" evaluator_label={self.evaluator_label}, span_name={self.span_name})"
        )

    __str__ = __repr__


class EvaluatorRunnerSampler:
    SAMPLING_RULES_ENV_VAR = "DD_LLMOBS_EVALUATOR_SAMPLING_RULES"

    def __init__(self) -> None:
        self.rules = self.parse_rules()

    def sample(self, evaluator_label: str, span: _LLMObsSpan) -> bool:
        for rule in self.rules:
            if rule.matches(evaluator_label=evaluator_label, span_name=span.name):
                # LLMObs spans are concrete ddtrace spans (SpanData subclasses) at runtime.
                return rule.sample(cast(SpanData, span))
        return True

    def parse_rules(self) -> list[EvaluatorRunnerSamplingRule]:
        rules = []

        sampling_rules_str = env.get(self.SAMPLING_RULES_ENV_VAR)
        telemetry_writer.add_configuration(self.SAMPLING_RULES_ENV_VAR, sampling_rules_str, origin="env_var")

        def parsing_failed_because(msg: str, maybe_throw_this: type[Exception]) -> None:
            telemetry_writer.add_log(
                TELEMETRY_LOG_LEVEL.ERROR, message=f"Evaluator sampling parsing failure because: {msg}"
            )
            telemetry_writer.add_count_metric(
                namespace=TELEMETRY_NAMESPACE.MLOBS,
                name="evaluators.error",
                value=1,
                tags=(("reason", "sampling_rule_parsing_failure"),),
            )
            if config._raise:
                raise maybe_throw_this(msg)
            logger.warning(msg, exc_info=True)

        if not sampling_rules_str:
            return []
        try:
            json_rules = json.loads(sampling_rules_str)
        except JSONDecodeError:
            parsing_failed_because(f"Failed to parse evaluator sampling rules of: `{sampling_rules_str}`", ValueError)
            return []

        if not isinstance(json_rules, list):
            parsing_failed_because("Evaluator sampling rules must be a list of dictionaries", ValueError)
            return []

        for rule in json_rules:
            if "sample_rate" not in rule:
                parsing_failed_because(f"No sample_rate provided for sampling rule: {json.dumps(rule)}", KeyError)
                continue
            try:
                sample_rate = float(rule[EvaluatorRunnerSamplingRule.SAMPLE_RATE_KEY])
            except ValueError:
                parsing_failed_because(f"sample_rate is not a float for rule: {json.dumps(rule)}", KeyError)
                continue
            span_name = rule.get(EvaluatorRunnerSamplingRule.SPAN_NAME_KEY, EvaluatorRunnerSamplingRule.NO_RULE)
            evaluator_label = rule.get(
                EvaluatorRunnerSamplingRule.EVALUATOR_LABEL_KEY, EvaluatorRunnerSamplingRule.NO_RULE
            )
            telemetry_writer.add_distribution_metric(
                TELEMETRY_NAMESPACE.MLOBS,
                "evaluators.rule_sample_rate",
                sample_rate,
                tags=(("evaluator_label", evaluator_label), ("span_name", span_name)),
            )
            rules.append(EvaluatorRunnerSamplingRule(sample_rate, evaluator_label, span_name))
        return rules
