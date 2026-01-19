"""
Request/response schemas for the LLMObs dev server.

Uses TypedDict and manual validation to avoid introducing Pydantic as a dependency.
"""

import json
import time
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Union

from typing_extensions import TypedDict


class ValidationError(Exception):
    """Raised when request validation fails."""


class EvalRequest(TypedDict, total=False):
    """Request body for /eval endpoint."""

    evaluator_name: str  # Required
    input_data: Dict[str, Any]  # Required
    output_data: Any  # Required
    expected_output: Any  # Required
    config: Optional[Dict[str, Any]]  # Optional runtime parameters
    tags: Optional[Dict[str, str]]  # Optional tags


class EvalResponse(TypedDict, total=False):
    """
    Response format matching LLMObsEvaluationMetricEvent.

    This format is compatible with Datadog's evaluation metric events.
    """

    label: str  # Evaluator name
    metric_type: str  # "categorical", "score", "boolean"
    categorical_value: Optional[str]
    score_value: Optional[float]
    boolean_value: Optional[bool]
    timestamp_ms: int
    error: Optional[str]  # Error message if evaluation failed
    tags: Optional[List[str]]


class EvaluatorListItem(TypedDict):
    """Single evaluator in the list response."""

    name: str
    description: Optional[str]
    accepts_config: bool


class ListResponse(TypedDict):
    """Response body for /list endpoint."""

    evaluators: List[EvaluatorListItem]


def parse_eval_request(request_data: Union[str, bytes, Dict]) -> EvalRequest:
    """
    Parse and validate request body for /eval endpoint.

    Args:
        request_data: Raw request body (JSON string, bytes, or dict).

    Returns:
        Validated EvalRequest.

    Raises:
        ValidationError: If validation fails.
    """
    # Handle different input types
    if isinstance(request_data, (str, bytes)):
        try:
            data = json.loads(request_data)
        except json.JSONDecodeError as e:
            raise ValidationError(f"Invalid JSON: {e}")
    else:
        data = request_data

    if not isinstance(data, dict):
        raise ValidationError("Request body must be a JSON object")

    # Required fields
    required_fields = ["evaluator_name", "input_data", "output_data", "expected_output"]
    for field in required_fields:
        if field not in data:
            raise ValidationError(f"'{field}' is required")

    # Validate evaluator_name
    evaluator_name = data["evaluator_name"]
    if not isinstance(evaluator_name, str):
        raise ValidationError(f"evaluator_name must be a string, got {type(evaluator_name).__name__}")
    if not evaluator_name or len(evaluator_name) > 256:
        raise ValidationError("evaluator_name must be 1-256 characters")

    # Validate input_data
    input_data = data["input_data"]
    if not isinstance(input_data, dict):
        raise ValidationError(f"input_data must be a dictionary, got {type(input_data).__name__}")

    # Build validated request
    parsed: EvalRequest = {
        "evaluator_name": evaluator_name,
        "input_data": input_data,
        "output_data": data["output_data"],  # Any type allowed
        "expected_output": data["expected_output"],  # Any type allowed
    }

    # Optional fields - validate dict type if present and non-null
    for field in ("config", "tags"):
        if field in data:
            value = data[field]
            if value is not None and not isinstance(value, dict):
                raise ValidationError(f"{field} must be a dictionary or null, got {type(value).__name__}")
            parsed[field] = value

    return parsed


def result_to_eval_response(label: str, result: Any, tags: Optional[List[str]] = None) -> EvalResponse:
    """
    Convert evaluator result to EvalResponse format.

    This follows the same type inference logic as ddtrace.llmobs._experiment.py.

    Args:
        label: Evaluator name.
        result: Raw result from evaluator function.
        tags: Optional tags to include.

    Returns:
        EvalResponse with appropriate metric_type and value fields.
    """
    response: EvalResponse = {
        "label": label,
        "timestamp_ms": int(time.time() * 1000),
    }

    if tags:
        response["tags"] = tags

    # Determine metric_type from result type
    if result is None:
        response["metric_type"] = "categorical"
        response["categorical_value"] = "null"
    elif isinstance(result, bool):
        # Check bool before int since bool is a subclass of int in Python
        response["metric_type"] = "boolean"
        response["boolean_value"] = result
    elif isinstance(result, (int, float)):
        response["metric_type"] = "score"
        response["score_value"] = float(result)
    else:
        response["metric_type"] = "categorical"
        response["categorical_value"] = str(result).lower()

    return response


def error_response(label: str, error: str) -> EvalResponse:
    """Create an error response."""
    return {
        "label": label,
        "metric_type": "categorical",
        "error": error,
        "timestamp_ms": int(time.time() * 1000),
    }
