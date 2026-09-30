"""
Exposure event building for Feature Flag evaluation results.
"""

import time
from typing import Optional

from openfeature.evaluation_context import EvaluationContext

from ddtrace.internal.logger import get_logger
from ddtrace.internal.openfeature.writer import ExposureEvent


logger = get_logger(__name__)


def build_exposure_event(
    flag_key: str,
    variant_key: Optional[str],
    allocation_key: Optional[str],
    evaluation_context: Optional[EvaluationContext],
    serial_id: Optional[int] = None,
) -> Optional[ExposureEvent]:
    """
    Build an exposure event that will be batched and sent with context.

    Individual events are collected and sent in batches with shared context
    (service, env, version) to the EVP proxy intake endpoint.

    Args:
        flag_key: The feature flag key
        variant_key: The variant key returned by the evaluation
        allocation_key: The allocation key
        evaluation_context: The evaluation context with subject information
        serial_id: Serial id of the split the subject landed in. The intake resolves it
            back to the holdout the allocation was generated from, which an exposure
            does not otherwise record.
    """
    # Validate required fields
    if not flag_key:
        logger.debug("Cannot build exposure event: flag_key is required")
        return None

    if not variant_key:
        logger.debug("Cannot build exposure event: variant_key is required")
        return None

    if not allocation_key:
        logger.debug("Cannot build exposure event: allocation_key is required")
        return None

    evaluation_context = evaluation_context or EvaluationContext()

    # Build the exposure event
    exposure_event: ExposureEvent = {
        "timestamp": int(time.time() * 1000),  # milliseconds since epoch
        "allocation": {"key": allocation_key},
        "flag": {"key": flag_key},
        "variant": {"key": variant_key},
        "subject": {
            "id": evaluation_context.targeting_key or "",
            "attributes": evaluation_context.attributes or {},
        },
    }

    if serial_id is not None:
        exposure_event["serial_id"] = serial_id

    return exposure_event
