"""
Runtime Coverage Writer
"""

from typing import Optional

from ddtrace.internal.ci_visibility.writer import CIVisibilityWriter
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


# Type alias: RuntimeCoverageWriter is just CIVisibilityWriter with coverage enabled
# This maintains backward compatibility while maximizing code reuse
RuntimeCoverageWriter = CIVisibilityWriter


# Global singleton writer instance
_RUNTIME_COVERAGE_WRITER: Optional[CIVisibilityWriter] = None


def get_runtime_coverage_writer() -> Optional[CIVisibilityWriter]:
    """
    Get the global runtime coverage writer instance.

    Returns None if runtime coverage is not initialized.
    """
    return _RUNTIME_COVERAGE_WRITER


def initialize_runtime_coverage_writer() -> bool:
    """
    Initialize and start the global runtime coverage writer.

    This should be called once at startup if DD_CIVISIBILITY_REQUEST_COVERAGE is enabled.

    AGGRESSIVE CODE REUSE: Uses CIVisibilityWriter with coverage_enabled=True instead of
    a separate RuntimeCoverageWriter class. The event client is created but unused,
    which is acceptable for the benefit of eliminating 130+ lines of duplicated code.

    Returns:
        True if initialization was successful, False otherwise
    """
    global _RUNTIME_COVERAGE_WRITER

    if _RUNTIME_COVERAGE_WRITER is not None:
        log.debug("Runtime coverage writer already initialized")
        return True

    try:
        # Use CIVisibilityWriter configured for runtime coverage:
        # - use_evp=True: Use EVP proxy mode
        # - coverage_enabled=True: Create coverage client
        # - itr_suite_skipping_mode=False: Include span_id for per-request coverage
        # - sync_mode=False: Async writing for production
        # Note: Event client is created but unused (harmless for coverage-only operation)
        _RUNTIME_COVERAGE_WRITER = CIVisibilityWriter(
            use_evp=True,
            coverage_enabled=True,
            itr_suite_skipping_mode=False,
            sync_mode=False,
            reuse_connections=True,
            report_metrics=False,
        )
        _RUNTIME_COVERAGE_WRITER.start()
        log.info("Runtime coverage writer started successfully (using CIVisibilityWriter)")
        return True
    except Exception as e:
        log.warning("Failed to initialize runtime coverage writer: %s", e, exc_info=True)
        _RUNTIME_COVERAGE_WRITER = None
        return False


def stop_runtime_coverage_writer(timeout: Optional[float] = None) -> None:
    """
    Stop the global runtime coverage writer and flush any pending data.

    Args:
        timeout: Maximum time to wait for flush (default: 5 seconds)
    """
    global _RUNTIME_COVERAGE_WRITER

    if _RUNTIME_COVERAGE_WRITER is None:
        return

    try:
        log.debug("Stopping runtime coverage writer")
        _RUNTIME_COVERAGE_WRITER.stop(timeout=timeout or 5.0)
        _RUNTIME_COVERAGE_WRITER = None
        log.info("Runtime coverage writer stopped")
    except Exception as e:
        log.warning("Error stopping runtime coverage writer: %s", e, exc_info=True)
