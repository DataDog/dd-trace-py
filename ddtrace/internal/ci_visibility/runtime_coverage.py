"""
Runtime Request Coverage

This module provides functionality for collecting and sending coverage data for individual
runtime requests (e.g., HTTP requests to WSGI/ASGI applications) to the CI Visibility
coverage intake endpoint.

This is based on, but separate from test coverage.
"""

from pathlib import Path
from typing import List  # noqa:F401
from typing import Optional  # noqa:F401
from typing import TYPE_CHECKING  # noqa:F401

from ddtrace.internal.ci_visibility.api._coverage_data import CoverageFilePayload
from ddtrace.internal.ci_visibility.api._coverage_data import TestVisibilityCoverageData
from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
from ddtrace.internal.ci_visibility.coverage_utils import get_coverage_root_dir
from ddtrace.internal.ci_visibility.runtime_coverage_writer import get_runtime_coverage_writer
from ddtrace.internal.compat import PYTHON_VERSION_INFO
from ddtrace.internal.coverage.code import ModuleCodeCollector
from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace.trace import Span


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

        # Determine root directory for coverage collection (shared with test coverage)
        root_dir = get_coverage_root_dir()

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


def build_runtime_coverage_payload(coverage_ctx, root_dir: Path) -> Optional[List[CoverageFilePayload]]:
    """
    Build a coverage payload from coverage context for runtime request coverage.

    Reuses TestVisibilityCoverageData for consistent payload formatting.

    Args:
        coverage_ctx: Coverage context from CollectInContext
        root_dir: Root directory for relative path resolution

    Returns:
        List of file coverage dicts: [{"filename": str, "bitmap": bytes}, ...]
    """
    try:
        covered_lines_dict = coverage_ctx.get_covered_lines()
        if not covered_lines_dict:
            return None

        coverage_data = TestVisibilityCoverageData()
        coverage_data.add_covered_files(covered_lines_dict)
        return coverage_data.build_payload(root_dir).get("files")

    except Exception as e:
        log.debug("Failed to build runtime coverage payload: %s", e, exc_info=True)
        return None


def send_runtime_coverage(span: "Span", files: List[CoverageFilePayload]) -> bool:
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
        # DEBUG: If DD_TRACE_DEBUG is enabled, print payload to stdout instead of sending
        import os

        if os.getenv("DD_TRACE_DEBUG"):
            _print_runtime_coverage_debug(span, files)
            return True
        # END_DEBUG

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


# DEBUG: Delete-me
def _print_runtime_coverage_debug(
    span: "Span", files: List[CoverageFilePayload]
) -> None:
    """
    Print runtime coverage payload to stdout for debugging when DD_TRACE_DEBUG is enabled.

    Args:
        span: The request span
        files: List of file coverage data
    """
    import base64
    import json

    try:
        # Convert bitmap bytes to base64 for JSON serialization
        from typing import Any
        from typing import Dict

        files_serializable: List[Dict[str, Any]] = []
        for file_data in files:
            file_copy: Dict[str, Any] = dict(
                file_data
            )  # Convert TypedDict to regular dict
            if "bitmap" in file_copy and isinstance(
                file_copy["bitmap"], (bytes, bytearray)
            ):
                file_copy["bitmap"] = base64.b64encode(file_copy["bitmap"]).decode(
                    "ascii"
                )
            files_serializable.append(file_copy)

        # Always use the 64-bit trace_id (lower 64 bits) to match APM trace correlation
        # This matches the x-datadog-trace-id header format used in distributed tracing
        payload: Dict[str, Any] = {
            "trace_id": span._trace_id_64bits,
            "span_id": span.parent_id,
            "test_id": span.get_tag("_dd.p.test_id"),
            "session_id": span.get_tag("_dd.p.test_session_id"),
            "suite_id": span.get_tag("_dd.p.test_suite_id"),
            "files": files_serializable,
        }

        # Print to stdout in a parseable format
        print(f"RUNTIME_COVERAGE_PAYLOAD: {json.dumps(payload)}", flush=True)
    except Exception as e:
        log.debug("Failed to print runtime coverage debug output: %s", e, exc_info=True)
