"""
Runtime Request Coverage

This module provides functionality for collecting and sending coverage data for individual
runtime requests (e.g., HTTP requests to WSGI/ASGI applications) to the CI Visibility
coverage intake endpoint.

This is based on, but separate from test coverage.
"""

import os
from pathlib import Path
from typing import Dict  # noqa:F401
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401

import ddtrace
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.runtime_coverage_writer import get_runtime_coverage_writer
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def is_runtime_coverage_supported() -> bool:
    """Check if runtime request coverage is supported (Python 3.12+)."""
    return PYTHON_VERSION_INFO >= (3, 12)


def initialize_runtime_coverage() -> bool:
    """
    Initialize runtime request coverage collection.

    This should be called at startup if DD_TRACE_RUNTIME_COVERAGE_ENABLED is set.
    Returns True if initialization was successful, False otherwise.
    """
    if not is_runtime_coverage_supported():
        log.warning(
            "Runtime request coverage requires Python 3.12+, "
            "but Python %d.%d is being used. Coverage collection will be disabled.",
            PYTHON_VERSION_INFO[0],
            PYTHON_VERSION_INFO[1],
        )
        return False

    try:
        from ddtrace.internal.coverage.installer import install

        # Determine root directory for coverage collection
        root_dir = Path(os.getcwd())

        # Install the coverage collector
        install(include_paths=[root_dir], collect_import_time_coverage=True)

        # Verify instance was created
        if ModuleCodeCollector._instance is None:
            log.warning("Failed to initialize coverage collector instance")
            return False

        log.info("Runtime request coverage initialized successfully")
        return True

    except Exception as e:
        log.warning("Failed to initialize runtime request coverage: %s", e, exc_info=True)
        return False


def build_runtime_coverage_payload(root_dir: Path, trace_id: int, span_id: int) -> Optional[Dict]:
    """
    Build a coverage payload from ModuleCodeCollector for runtime request coverage.

    Args:
        root_dir: Root directory for relative path resolution
        trace_id: Trace ID for correlation
        span_id: Span ID for correlation

    Returns:
        Dictionary with coverage files, trace_id, and span_id, or None if no coverage data
    """
    try:
        # Get coverage files from ModuleCodeCollector
        files = ModuleCodeCollector.report_seen_lines(root_dir, include_imported=True)

        if not files:
            return None

        # Return payload dict with trace/span IDs for correlation
        return {
            "trace_id": trace_id,
            "span_id": span_id,
            "files": files,
        }

    except Exception as e:
        log.debug("Failed to build runtime coverage payload: %s", e, exc_info=True)
        return None


def send_runtime_coverage(span: ddtrace.trace.Span, files: List[Dict]) -> bool:
    """
    Send runtime coverage data to citestcov intake using the RuntimeCoverageWriter.

    This follows the natural flow used by test coverage: set coverage data as a struct tag
    on the span, then write it to the dedicated coverage writer which handles batching,
    encoding, retries, and sending to the correct endpoint.

    Args:
        span: The request span to attach coverage data to
        files: List of file coverage data in CI Visibility format

    Returns:
        True if enqueued successfully, False otherwise
    """
    try:
        # Get the global runtime coverage writer
        writer = get_runtime_coverage_writer()
        if writer is None:
            log.debug("Runtime coverage writer not initialized")
            return False

        # Set coverage data as struct tag on the span (matches TestVisibilityItemBase pattern)
        # The coverage encoder will extract this tag when encoding
        span._set_struct_tag(COVERAGE_TAG_NAME, {"files": files})

        # Write the span to the coverage writer
        writer.write([span])

        log.debug("Runtime coverage span enqueued for trace_id=%s, span_id=%s", span.trace_id, span.span_id)
        return True

    except Exception as e:
        log.debug("Failed to send runtime coverage: %s", e, exc_info=True)
        return False
