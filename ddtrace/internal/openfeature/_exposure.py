"""
Exposure event building for Feature Flag evaluation results.
"""

import time
from typing import Any
from typing import Dict
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
) -> Optional[ExposureEvent]:
    """
    Build an exposure event that will be batched and sent with context.

    Individual events are collected and sent in batches with shared context
    (service, env, version) to the EVP proxy intake endpoint.

    Args:
        flag_key: The feature flag key
        variant_key: The variant key returned by the evaluation
        allocation_key: The allocation key (same as variant_key in basic cases)
        evaluation_context: The evaluation context with subject information
    """
    # Validate required fields
    if not flag_key:
        logger.debug("Cannot build exposure event: flag_key is required")
        return None

    if variant_key is None:
        variant_key = ""

    # Build subject from evaluation context
    subject = _build_subject(evaluation_context)
    if not subject:
        logger.debug("Cannot build exposure event: valid subject is required")
        return None

    # Use variant_key as allocation_key if not explicitly provided
    if allocation_key is None:
        allocation_key = variant_key

    # Build the exposure event
    exposure_event: ExposureEvent = {
        "timestamp": int(time.time() * 1000),  # milliseconds since epoch
        "allocation": {"key": allocation_key},
        "flag": {"key": flag_key},
        "variant": {"key": variant_key},
        "subject": subject,
    }

    return exposure_event


def _build_subject(evaluation_context: Optional[EvaluationContext]) -> Optional[Dict[str, Any]]:
    """
    Build subject object from OpenFeature EvaluationContext.

    The subject must have at minimum an 'id' field.

    Args:
        evaluation_context: The OpenFeature evaluation context

    Returns:
        Dictionary with subject information, or None if id cannot be determined
    """
    if evaluation_context is None:
        return None

    # Get targeting_key as the subject id
    subject_id = evaluation_context.targeting_key
    if not subject_id:
        logger.debug("evaluation_context missing targeting_key for subject.id")
        return None

    subject: Dict[str, Any] = {"id": subject_id}

    # Add optional subject type if available in attributes
    attributes = evaluation_context.attributes or {}
    if "subject_type" in attributes:
        subject["type"] = str(attributes["subject_type"])

    # Add remaining attributes (excluding subject_type which we already handled)
    remaining_attrs = {k: v for k, v in attributes.items() if k != "subject_type"}
    if remaining_attrs:
        subject["attributes"] = remaining_attrs

    return subject
