import math
import time

from ddtrace import config


class RagasFaithfulnessEvaluator:
    name = "ragas_faithfulness"
    metric_type = "score"

    @classmethod
    def evaluate(cls, span):
        return {
            "span_id": span.get("span_id"),
            "trace_id": span.get("trace_id"),
            "score_value": 1,
            "ml_app": config._llmobs_ml_app,
            "timestamp_ms": math.floor(time.time() * 1000),
            "metric_type": cls.metric_type,
            "label": cls.name,
        }
