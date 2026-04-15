"""Shared utilities for coverage report uploads across V2 and V3 plugins."""

import time
import typing as t


def create_coverage_report_event(
    coverage_format: str,
    tags: t.Optional[dict[str, str]] = None,
) -> dict[str, t.Any]:
    """
    Create the event JSON payload for coverage report upload.

    This function is used by Pytest plugins V2 (CI Visibility), V3 (Testing)

    Args:
        coverage_format: The format of the coverage report (e.g., 'lcov', 'cobertura')
        tags: Tags to include. Only git.*, ci.*, and pr.* prefixed tags are included in the event.

    Returns:
        Dictionary with event data ready for upload
    """
    event: dict[str, t.Any] = {
        "type": "coverage_report",
        "format": coverage_format,
        "timestamp": int(time.time() * 1000),
    }

    # Add only git.*, ci.*, and pr.* tags.
    if tags:
        for key, value in tags.items():
            if not value:
                continue

            if key.startswith(("git.", "ci.", "pr.")):
                event[key] = value

    return event
