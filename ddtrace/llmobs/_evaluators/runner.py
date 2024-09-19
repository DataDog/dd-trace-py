import atexit
from concurrent import futures
import json
import os
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace import Span
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.sampling_rule import SamplingRule

from .ragas.faithfulness import RagasFaithfulnessEvaluator


logger = get_logger(__name__)

SUPPORTED_EVALUATORS = {
    "ragas_faithfulness": RagasFaithfulnessEvaluator,
}


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


class EvaluatorRunner(PeriodicService):
    """Base class for evaluating LLM Observability span events"""

    def __init__(self, interval: float, _evaluation_metric_writer=None):
        super(EvaluatorRunner, self).__init__(interval=interval)
        self._lock = forksafe.RLock()
        self._buffer = []  # type: List[Tuple[Dict, Span]]
        self._buffer_limit = 1000

        self._evaluation_metric_writer = _evaluation_metric_writer

        self.executor = futures.ThreadPoolExecutor()
        self.evaluators = []

        evaluator_str = os.getenv("_DD_LLMOBS_EVALUATORS")
        if evaluator_str is not None:
            evaluators = evaluator_str.split(",")
            for evaluator in evaluators:
                if evaluator in SUPPORTED_EVALUATORS:
                    self.evaluators.append(SUPPORTED_EVALUATORS[evaluator])

        self.sampler = EvaluatorSampler()

    def start(self, *args, **kwargs):
        super(EvaluatorRunner, self).start()
        logger.debug("started %r to %r", self.__class__.__name__)
        atexit.register(self.on_shutdown)

    def on_shutdown(self):
        self.executor.shutdown()

    def enqueue(self, span_event: Dict, span: Span) -> None:
        with self._lock:
            if len(self._buffer) >= self._buffer_limit:
                logger.warning(
                    "%r event buffer full (limit is %d), dropping event", self.__class__.__name__, self._buffer_limit
                )
                return
            self._buffer.append((span_event, span))

    def periodic(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            span_batch = self._buffer  # type: List[Tuple[Dict, Span]]
            self._buffer = []

        try:
            evaluation_metrics = self.run(span_batch)
            for metric in evaluation_metrics:
                if metric is not None:
                    self._evaluation_metric_writer.enqueue(metric)
        except RuntimeError as e:
            logger.debug("failed to run evaluation: %s", e)

    def run(self, span_batch: List[Tuple[Dict, Span]]) -> List[Dict]:
        batches_of_results = []

        for evaluator in self.evaluators:
            batches_of_results.append(
                self.executor.map(
                    evaluator.evaluate,
                    [span_event for span_event, span in span_batch if self.sampler.sample(evaluator.label, span)],
                )
            )

        results = []
        for batch in batches_of_results:
            for result in batch:
                results.append(result)

        return results
