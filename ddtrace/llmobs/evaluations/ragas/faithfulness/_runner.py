import atexit
from concurrent import futures
import math
from threading import Event
import time
from typing import Dict
from typing import List
from typing import Optional

from ddtrace import config
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.periodic import PeriodicService

from ....utils import EvaluationMetric
from ....utils import LLMObsSpanContext
from ._scorer import score_faithfulness


logger = get_logger(__name__)


class RagasFaithfulnessEvaluationRunner(PeriodicService):
    """Base class for evaluating LLM Observability span events"""

    def __init__(self, interval: float, writer=None, llmobs_instance=None):
        super(RagasFaithfulnessEvaluationRunner, self).__init__(interval=interval)
        self.llmobs_instance = llmobs_instance
        self._lock = forksafe.RLock()
        self._buffer = []  # type: List[LLMObsSpanContext]
        self._buffer_limit = 1000

        self._llmobs_eval_metric_writer = writer
        self.spans = []  # type: List[LLMObsSpanContext]

        self.name = "ragas.faithfulness"

        self.shutdown_event = Event()
        self.executor = futures.ThreadPoolExecutor()

    def start(self, *args, **kwargs):
        super(RagasFaithfulnessEvaluationRunner, self).start()
        logger.debug("started %r to %r", self.__class__.__name__)
        atexit.register(self.on_shutdown)

    def on_shutdown(self):
        self.shutdown_event.set()
        self.executor.shutdown(cancel_futures=True)

    def recreate(self):
        return self.__class__(
            interval=self._interval, writer=self._llmobs_eval_metric_writer, llmobs_instance=self.llmobs_instance
        )

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

    def periodic(self) -> None:
        with self._lock:
            if not self._buffer:
                return
            events = self._buffer
            self._buffer = []

        try:
            if not self.shutdown_event.is_set():
                evaluation_metrics = self.run(events)
                for metric in evaluation_metrics:
                    if metric is not None:
                        self._llmobs_eval_metric_writer.enqueue(metric.model_dump())
            else:
                logger.warning("app shutdown detected, not running evaluations")
        except RuntimeError as e:
            logger.debug("failed to run evaluation: %s", e)

    def run(self, spans: List[LLMObsSpanContext]) -> List[EvaluationMetric]:
        def score_and_return_evaluation(span) -> Optional[EvaluationMetric]:
            try:
                score, faithfulness_list, exported_ragas_span = score_faithfulness(
                    span, llmobs_instance=self.llmobs_instance, shutdown_event=self.shutdown_event
                )
                if math.isnan(score):
                    return None

                return EvaluationMetric(
                    label="ragas.faithfulness",
                    span_id=span.span_id,
                    trace_id=span.trace_id,
                    score_value=score,
                    ml_app=config._llmobs_ml_app,
                    timestamp_ms=math.floor(time.time() * 1000),
                    metric_type="score",
                    tags=[
                        "evaluation_trace_id:{}".format(
                            exported_ragas_span.get("trace_id") if exported_ragas_span.get("trace_id") else ""
                        ),
                        "evaluation_span_id:{}".format(
                            exported_ragas_span.get("span_id") if exported_ragas_span.get("span_id") else ""
                        ),
                    ],
                )
            except RuntimeError as e:
                logger.error("Failed to run evaluation: %s", e)
                return None

        results = self.executor.map(score_and_return_evaluation, spans)
        return [result for result in results if result is not None]
