import atexit
from concurrent import futures
import os
from typing import Dict

from ddtrace import Span
from ddtrace.internal import forksafe
from ddtrace.internal import service
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService
from ddtrace.internal.service import ServiceStatus
from ddtrace.llmobs._evaluators.ragas.faithfulness import RagasFaithfulnessEvaluator
from ddtrace.llmobs._evaluators.sampler import EvaluatorRunnerSampler


logger = get_logger(__name__)

SUPPORTED_EVALUATORS = {
    "ragas_faithfulness": RagasFaithfulnessEvaluator,
}


class EvaluatorRunner(PeriodicService):
    """Base class for evaluating LLM Observability span events
    This class
    1. parses active evaluators from the environment and initializes these evaluators
    2. triggers evaluator runs over buffered finished spans on each `periodic` call
    """

    def __init__(self, interval: float, llmobs_service=None, evaluators=None):
        super(EvaluatorRunner, self).__init__(interval=interval)
        self._lock = forksafe.RLock()
        self._buffer = []  # type: list[tuple[Dict, Span]]
        self._buffer_limit = 1000

        self.llmobs_service = llmobs_service
        self.executor = futures.ThreadPoolExecutor()
        self.sampler = EvaluatorRunnerSampler()
        self.evaluators = [] if evaluators is None else evaluators

        if len(self.evaluators) > 0:
            return

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
        if self.status == service.ServiceStatus.STOPPED:
            return
        super(EvaluatorRunner, self).stop()

    def recreate(self) -> "EvaluatorRunner":
        return self.__class__(
            interval=self._interval,
            llmobs_service=self.llmobs_service,
            evaluators=self.evaluators,
        )

    def on_shutdown(self):
        self.executor.shutdown()

    def enqueue(self, span_event: Dict, span: Span) -> None:
        if self.status == ServiceStatus.STOPPED:
            return
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
            span_events_and_spans = self._buffer  # type: list[tuple[Dict, Span]]
            self._buffer = []

        try:
            self.run(span_events_and_spans)
        except RuntimeError as e:
            logger.debug("failed to run evaluation: %s", e)

    def run(self, span_events_and_spans):
        for evaluator in self.evaluators:
            self.executor.map(
                lambda span: evaluator.run_and_submit_evaluation(span),
                [
                    span_event
                    for span_event, span in span_events_and_spans
                    if self.sampler.sample(evaluator.LABEL, span)
                ],
            )
