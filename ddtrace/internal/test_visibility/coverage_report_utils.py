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
        tags: Tags to include. Only git.* and ci.* prefixed tags are included in the event.

    Returns:
        Dictionary with event data ready for upload
    """
    event: dict[str, t.Any] = {
        "type": "coverage_report",
        "format": coverage_format,
        "timestamp": int(time.time() * 1000),
    }

    # Add only git.* and ci.* tags
    if tags:
        for key, value in tags.items():
            if not value:
                continue

            # Handle PR number special case: map to "pr.number"
            if key == "git.pull_request.number":
                event["pr.number"] = value
            # Only include git.* and ci.* prefixed tags
            elif key.startswith(("git.", "ci.")):
                event[key] = value

    return event
