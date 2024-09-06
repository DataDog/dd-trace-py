import asyncio
import atexit
import math
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
        self.name = "default-evaluation-runner"
        self.llmobs_instance = llmobs_instance
        self._lock = forksafe.RLock()
        self._buffer = []  # type: List[LLMObsSpanContext]
        self._buffer_limit = 1000

        self._llmobs_eval_metric_writer = writer
        self.spans = []  # type: List[LLMObsSpanContext]

        self.enabled = False
        self.name = "ragas.faithfulness"
        try:
            import asyncio  # noqa: F401

            from ragas.metrics import faithfulness  # noqa: F401

            self.enabled = True
        except ImportError:
            logger.warning("Failed to import ragas, skipping RAGAS evaluation runner")

    def start(self, *args, **kwargs):
        super(RagasFaithfulnessEvaluationRunner, self).start()
        logger.debug("started %r to %r", self.__class__.__name__)
        atexit.register(self.on_shutdown)

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
            evaluation_metrics = self.run(events)
            for metric in evaluation_metrics:
                self._llmobs_eval_metric_writer.enqueue(metric.model_dump())
        except RuntimeError as e:
            logger.debug("failed to run evaluation: %s", e)

    def translate_span(self, span: LLMObsSpanContext) -> Optional[dict]:
        if span.meta.input.prompt is None:
            logger.debug("Skipping span %s, no prompt found", span.span_id)
            return None

        prompt = span.meta.input.prompt
        question = prompt.variables.get("question")
        context_str = prompt.variables.get("context")

        if not context_str:
            raise ValueError("no context found")
        if not question:
            if not question:
                logger.warning("Skipping span %s, no question found", span.span_id)
                return None
        if span.meta.output.messages and len(span.meta.output.messages) > 0:
            answer = span.meta.output.messages[-1]["content"]
        else:
            return None
        return {
            "question": question,
            "answer": answer,
            "context_str": context_str,
        }

    def run(self, spans: List[LLMObsSpanContext]) -> List[EvaluationMetric]:
        if not self.enabled:
            logger.warning("RagasFaithfulnessRunner is not enabled")
            return []

        async def score_and_return_evaluation(span):
            inps = self.translate_span(span)
            if inps is None:
                return None
            score = await score_faithfulness(**inps, llmobs_instance=self.llmobs_instance)
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
                # tags .. todo
            )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()

        asyncio.set_event_loop(loop)

        async def score_spans():
            return await asyncio.gather(*[score_and_return_evaluation(span) for span in spans])

        results = loop.run_until_complete(score_spans())

        return [result for result in results if result is not None]
