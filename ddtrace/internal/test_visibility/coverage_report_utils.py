"""Shared utilities for coverage report uploads across V2 and V3 plugins."""

import logging
import os
import time
import typing as t

from ddtrace.internal.utils.cache import cached


# AIDEV-NOTE: Use stdlib logging and environment access here. Importing ddtrace.internal.logger or
# ddtrace.internal.settings creates circular imports because this utility is shared by both the
# legacy CI Visibility and Testing plugin dependency trees.
log = logging.getLogger(__name__)

_CODE_COVERAGE_FLAGS_ENV_VAR = "DD_CODE_COVERAGE_FLAGS"
_MAX_CODE_COVERAGE_FLAGS = 32


def _parse_code_coverage_flags(raw_flags: t.Optional[str]) -> tuple[str, ...]:
    if not raw_flags:
        return ()

    flags = tuple(flag.strip() for flag in raw_flags.split(",") if flag.strip())
    if len(flags) > _MAX_CODE_COVERAGE_FLAGS:
        log.warning(
            "Code coverage report flags will be omitted because %d flags were provided, exceeding the maximum of %d",
            len(flags),
            _MAX_CODE_COVERAGE_FLAGS,
        )
        return ()

    return flags


@cached(maxsize=1)
def _get_code_coverage_flags() -> tuple[str, ...]:
    return _parse_code_coverage_flags(os.environ.get(_CODE_COVERAGE_FLAGS_ENV_VAR))


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

    flags = _get_code_coverage_flags()
    if flags:
        event["report.flags"] = list(flags)

    # Add only git.*, ci.*, and pr.* tags.
    if tags:
        for key, value in tags.items():
            if not value:
                continue

            if key.startswith(("git.", "ci.", "pr.")):
                event[key] = value

    return event
