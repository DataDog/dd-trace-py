import atexit
from concurrent import futures
import os
from typing import Dict

from ddtrace.internal import forksafe
from ddtrace.internal import service
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
        if evaluator_str is None:
            return

        evaluators = evaluator_str.split(",")
        for evaluator in evaluators:
            if evaluator in SUPPORTED_EVALUATORS:
                self.evaluators.append(SUPPORTED_EVALUATORS[evaluator](llmobs_service=llmobs_service))

    def start(self, *args, **kwargs):
        if not self.evaluators:
            logger.debug("no evaluators configured, not starting %r", self.__class__.__name__)
            return
        super(EvaluatorRunner, self).start()
        logger.debug("started %r to %r", self.__class__.__name__)
        atexit.register(self.on_shutdown)

    def stop(self, *args, **kwargs):
        if not self.evaluators and self.status == service.ServiceStatus.STOPPED:
            logger.debug("no evaluators configured, not starting %r", self.__class__.__name__)
            return
        super(EvaluatorRunner, self).stop()

    def recreate(self) -> "EvaluatorRunner":
        return self.__class__(
            interval=self._interval,
            llmobs_service=self.llmobs_service,
        )

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
        for evaluator in self.evaluators:
            self.executor.map(lambda span: evaluator.run_and_submit_evaluation(span), spans)
