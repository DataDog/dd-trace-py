import math
import time
from typing import List
from typing import Optional

from ddtrace import config
from ddtrace.internal.logger import get_logger

from ....utils import EvaluationMetric
from ....utils import LLMObsSpanContext
from ..._base_runner import LLMObsEvaluationRunner
from ._scorer import score_faithfulness


log = get_logger(__name__)


class RagasFaithfulnessRunner(LLMObsEvaluationRunner):
    def __init__(self, writer, llmobs_instance):
        super().__init__(
            interval=0.1,
            writer=writer,
            llmobs_instance=llmobs_instance,
        )
        self.enabled = False
        self.name = "ragas.faithfulness"
        try:
            import asyncio  # noqa: F401

            from ragas.metrics import faithfulness  # noqa: F401

            self.enabled = True
        except ImportError:
            log.warning("Failed to import ragas, skipping RAGAS evaluation runner")

    def translate_span(self, span: LLMObsSpanContext) -> Optional[dict]:
        if span.meta.input.prompt is None:
            log.debug("Skipping span %s, no prompt found", span.span_id)
            return None

        prompt = span.meta.input.prompt
        question = prompt.variables.get("question")
        context_str = prompt.variables.get("context")

        if not context_str:
            raise ValueError("no context found")
        if not question:
            if not question:
                log.warning("Skipping span %s, no question found", span.span_id)
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
            log.warning("RagasFaithfulnessRunner is not enabled")
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

        import asyncio
        import sys

        if sys.version_info < (3, 10):
            loop = asyncio.get_event_loop()
        else:
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.new_event_loop()

            asyncio.set_event_loop(loop)

        async def score_spans():
            return await asyncio.gather(*[score_and_return_evaluation(span) for span in spans])

        results = loop.run_until_complete(score_spans())

        return [result for result in results if result is not None]
