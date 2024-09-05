from typing import Dict
from typing import List

from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService

from ..utils import EvaluationMetric
from ..utils import LLMObsSpanContext


logger = get_logger(__name__)


class LLMObsEvaluationRunner(PeriodicService):
    """Base class for evaluating LLM Observability span events"""

    def __init__(self, interval: float, writer=None, llmobs_instance=None, sample_rate=1.0):
        super(LLMObsEvaluationRunner, self).__init__(interval=interval)
        self.name = "default-evaluation-runner"
        self.llmobs_instance = llmobs_instance
        self._lock = forksafe.RLock()
        self._buffer = []  # type: List[LLMObsSpanContext]
        self._buffer_limit = 1000

        self._llmobs_eval_metric_writer = writer
        self.spans = []  # type: List[LLMObsSpanContext]

    def start(self, *args, **kwargs):
        super(LLMObsEvaluationRunner, self).start()
        logger.debug("started %r", self.__class__.__name__)

    def enqueue(self, raw_span_event: Dict) -> None:
        with self._lock:
            if len(self._buffer) >= self._buffer_limit:
                logger.warning(
                    "%r event buffer full (limit is %d), dropping event", self.__class__.__name__, self._buffer_limit
                )
                return
            try:
                self._buffer.append(LLMObsSpanContext(**raw_span_event))
            except Exception as e:
                logger.error("Failed to validate span event for eval", e)

    def run(self, spans: List[LLMObsSpanContext]) -> List[EvaluationMetric]:
        raise NotImplementedError

    def periodic(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            events = self._buffer
            self._buffer = []

        try:
            evaluation_metrics = self.run(events)
            for metric in evaluation_metrics:
                self._llmobs_eval_metric_writer.enqueue(metric.model_dump())
        except RuntimeError as e:
            logger.debug("failed to run evaluation: %s", e)
