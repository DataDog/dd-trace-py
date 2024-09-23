import atexit
from concurrent import futures
import os
from typing import Dict

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService

from .ragas.faithfulness import RagasFaithfulnessEvaluator


logger = get_logger(__name__)

SUPPORTED_EVALUATORS = {
    "ragas_faithfulness": RagasFaithfulnessEvaluator,
}


class EvaluatorRunner(PeriodicService):
    """Base class for evaluating LLM Observability span events"""

    def __init__(self, interval: float, llmobs_service=None):
        super(EvaluatorRunner, self).__init__(interval=interval)
        self._lock = forksafe.RLock()
        self._buffer = []  # type: list[Dict]
        self._buffer_limit = 1000

        self.llmobs_service = llmobs_service
        self.executor = futures.ThreadPoolExecutor()
        self.evaluators = []

        evaluator_str = os.getenv("_DD_LLMOBS_EVALUATORS")
        if evaluator_str is not None:
            evaluators = evaluator_str.split(",")
            for evaluator in evaluators:
                if evaluator in SUPPORTED_EVALUATORS:
                    self.evaluators.append(SUPPORTED_EVALUATORS[evaluator](llmobs_service=llmobs_service))

    def start(self, *args, **kwargs):
        super(EvaluatorRunner, self).start()
        logger.debug("started %r to %r", self.__class__.__name__)
        atexit.register(self.on_shutdown)

    def on_shutdown(self):
        self.executor.shutdown()

    def enqueue(self, span_event: Dict) -> None:
        with self._lock:
            if len(self._buffer) >= self._buffer_limit:
                logger.warning(
                    "%r event buffer full (limit is %d), dropping event", self.__class__.__name__, self._buffer_limit
                )
                return

            self._buffer.append(span_event)

    def periodic(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            events = self._buffer
            self._buffer = []

        try:
            self.run(events)
        except RuntimeError as e:
            logger.debug("failed to run evaluation: %s", e)

    def run(self, spans):
        batches_of_results = []

        for evaluator in self.evaluators:
            batches_of_results.append(self.executor.map(lambda span: evaluator.evaluate(span), spans))

        results = []
        for batch in batches_of_results:
            for result in batch:
                results.append(result)

        return results
