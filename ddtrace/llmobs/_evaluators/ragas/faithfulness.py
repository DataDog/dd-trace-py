import math
import time


class RagasFaithfulnessEvaluator:
    LABEL = "ragas_faithfulness"
    METRIC_TYPE = "score"

    def __init__(self, llmobs_service):
        self.llmobs_service = llmobs_service

    def evaluate(self, span):
        self.llmobs_service.submit_evaluation(
            span_context={
                "span_id": span.get("span_id"),
                "trace_id": span.get("trace_id"),
            },
            label=RagasFaithfulnessEvaluator.LABEL,
            metric_type=RagasFaithfulnessEvaluator.METRIC_TYPE,
            value=1,
            timestamp_ms=math.floor(time.time() * 1000),
        )
