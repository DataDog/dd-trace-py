import math
import time
from typing import Optional


class RagasFaithfulnessEvaluator:
    LABEL = "ragas_faithfulness"
    METRIC_TYPE = "score"

    def __init__(self, llmobs_service):
        self.llmobs_service = llmobs_service

    def run(self, span: dict) -> None:
        if not span:
            return
        score_result = self.evaluate(span)
        if score_result:
            self.llmobs_service.submit_evaluation(
                span_context=span,
                label=RagasFaithfulnessEvaluator.LABEL,
                metric_type=RagasFaithfulnessEvaluator.METRIC_TYPE,
                value=score_result,
                timestamp_ms=math.floor(time.time() * 1000),
            )

    def evaluate(self, span: dict) -> Optional[float]:
        span_id, trace_id = span.get("span_id"), span.get("trace_id")
        if not span_id or not trace_id:
            return None
        return 1.0
