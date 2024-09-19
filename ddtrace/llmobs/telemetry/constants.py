from enum import Enum


LLMOBS_TELEMETRY_METRIC_NAMESPACE = "llmobs"


class LLMOBS_TELEMETRY(str, Enum):
    ACTIVATE_DISTRIBUTED_HEADERS = "activate_distributed_headers"
    ANNOTATIONS = "annotations"
    EXPORT_SPAN = "export_span"
    INIT_TIME = "init_time"
    INJECT_DISTRIBUTED_HEADERS = "inject_distributed_headers"
    SPANS_CREATED = "spans_created"
    SPANS_FINISHED = "spans_finished"
    SUBMIT_EVALUATIONS = "submit_evaluation"
    USER_FLUSH = "user_flush"
