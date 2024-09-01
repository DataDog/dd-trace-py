import math
import time
from typing import List
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.llmobs.utils import EvaluationMetric
from ddtrace.llmobs.utils import LLMObsSpanContext

from ._runners import LLMObsEvaluationRunner


log = get_logger(__name__)


class RagasRunner(LLMObsEvaluationRunner):
    def __init__(self, writer, metrics: Optional[List[str]] = None):
        super().__init__(interval=0.1, writer=writer)
        self.metrics = metrics
        self.datasets = None  # type: Optional[Dataset]
        self.evaluate = None
        try:
            from datasets import Dataset
            from ragas import evaluate
            from ragas.metrics import answer_relevancy
            from ragas.metrics import context_utilization
            from ragas.metrics import faithfulness

            if not metrics:
                self.metrics = [answer_relevancy, faithfulness, context_utilization]

            self.datasets = Dataset
            self.evaluate = evaluate
        except ImportError:
            log.warning("Failed to import ragas, skipping RAGAS evaluation runner")

    def translate_span(self, span: LLMObsSpanContext):
        if span.meta.input.prompt is None:
            log.warning("Skipping span %s, no prompt found", span.span_id)
            return None

        prompt = span.meta.input.prompt

        question = prompt.variables.get("question")
        context = prompt.variables.get("context")
        if not context:
            raise ValueError("no context found")
        if not question:
            if not question:
                log.warning("Skipping span %s, no question found", span.span_id)
                return None
        if span.meta.output.messages and len(span.meta.output.messages) > 0:
            answer = span.meta.output.messages[-1]["content"]
        else:
            return None
        return question, [context], answer

    def run(self, spans: List[LLMObsSpanContext]) -> List[EvaluationMetric]:
        if not self.datasets or not self.evaluate:
            log.warning("RAGAS evaluation runner not initialized")
            return []

        evaluation_metrics = []  # type: List[EvaluationMetric]
        for span in spans:
            translated = self.translate_span(span)
            if translated is None:
                log.debug("Failed to translate span for RAGAS eval")
                continue
            question, context, answer = translated
            data = {"question": [question], "answer": [answer], "contexts": [context]}
            data["question"].append(question)
            data["answer"].append(answer)
            data["contexts"].append(context)

            dataset = self.datasets.from_dict(data)
            if dataset.num_rows == 0:
                log.debug("No valid spans found for RAGAS eval")
                return evaluation_metrics
            results = self.evaluate(metrics=self.metrics, dataset=dataset, raise_exceptions=False)
            for k, v in results.items():
                if not math.isnan(v):
                    evaluation_metrics.append(
                        EvaluationMetric(
                            label="ragas.{}".format(k),
                            score_value=v,
                            metric_type="score",
                            ml_app=span.ml_app,
                            timestamp_ms=int(time.time() * 1000),
                            span_id=span.span_id,
                            trace_id=span.trace_id,
                        )
                    )
        return evaluation_metrics
