import time
from typing import List
from typing import Optional

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.llmobs.utils import EvaluationMetric
from ddtrace.llmobs.utils import LLMObsSpanContext

from ._runners import LLMObsEvaluationRunner


log = get_logger(__name__)


class RagasRunner(LLMObsEvaluationRunner):
    def __init__(self, writer, metrics: Optional[List[str]] = None):
        super().__init__(interval=0.1, writer=writer)
        self.metrics = metrics

    def run(self, spans: List[LLMObsSpanContext]) -> List[EvaluationMetric]:
        results = []
        for span in spans:
            results.append(
                EvaluationMetric(
                    label="ragas.faithfulness",
                    score_value=0.5,
                    metric_type="score",
                    span_id=span.span_id,
                    trace_id=span.trace_id,
                    ml_app=config._llmobs_ml_app,
                    timestamp_ms=round(time.time() * 1000),
                )
            )
        return results
