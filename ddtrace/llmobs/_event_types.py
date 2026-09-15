"""Shared span and experiment payload types, independent of writers and services.

Keep these contracts below the consumers that construct, inspect, and send events.
"""

from typing import Any
from typing import Mapping
from typing import Optional
from typing import Sequence
from typing import TypedDict
from typing import Union

from ddtrace.llmobs.types import ExperimentConfigType
from ddtrace.llmobs.types import _Meta
from ddtrace.llmobs.types import _SpanLink


# NOTE: Experiments accept general sequences/mappings, unlike the public JSONType.
JSONType = Union[str, int, float, bool, None, Sequence["JSONType"], Mapping[str, "JSONType"]]


class LLMObsSpanData(TypedDict, total=False):
    """Structure of LLMObs span data attached to APM spans."""

    name: str
    parent_id: str
    pagent_name: str
    pagent_span_id: str
    trace_id: str
    ml_app: str
    session_id: str
    tags: dict[str, str]
    metrics: dict[str, Any]
    span_links: list["_SpanLink"]
    config: "ExperimentConfigType"
    meta: _Meta
    _dd: dict[str, str]


class _LLMObsSpanEventOptional(TypedDict, total=False):
    session_id: str
    service: str
    status_message: str
    collection_errors: list[str]
    span_links: list["_SpanLink"]
    config: "ExperimentConfigType"


class LLMObsSpanEvent(_LLMObsSpanEventOptional):
    span_id: str
    trace_id: str
    parent_id: str
    tags: list[str]
    name: str
    start_ns: int
    duration: int
    status: str
    meta: _Meta
    metrics: dict[str, Any]
    _dd: dict[str, str]


class LLMObsExperimentEvalMetricEvent(TypedDict, total=False):
    metric_source: str
    span_id: str
    trace_id: str
    timestamp_ms: int
    metric_type: str
    label: str
    categorical_value: str
    score_value: float
    boolean_value: bool
    json_value: dict[str, JSONType]
    status: str
    error: Optional[dict[str, str]]
    tags: list[str]
    experiment_id: str
    reasoning: str
    assessment: str
    metadata: dict[str, JSONType]
    eval_source_type: str


class EvaluatorInferResponse(TypedDict, total=False):
    """Response from the evaluator_infer API endpoint."""

    value: JSONType
    assessment: Optional[str]
    reasoning: Optional[str]
    status: Optional[str]
