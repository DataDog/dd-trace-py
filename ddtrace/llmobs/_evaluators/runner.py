import atexit
from concurrent import futures
import os
from typing import Dict
from typing import List
from typing import Tuple

from ddtrace import Span
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService

from .ragas.faithfulness import RagasFaithfulnessEvaluator
from .sampler import EvaluatorSampler


logger = get_logger(__name__)

SUPPORTED_EVALUATORS = {
    "ragas_faithfulness": RagasFaithfulnessEvaluator,
}


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
