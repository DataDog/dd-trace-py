"""
Runtime Coverage Writer

A dedicated writer for sending runtime request coverage data to the citestcov intake endpoint.
This follows the same pattern as ExposureWriter and CIVisibilityWriter to properly manage
HTTP connections, retries, batching, and encoding.
"""
from typing import Optional

from ddtrace import config
from ddtrace.internal.logger import get_logger
from ddtrace.settings._agent import config as agent_config

from ..writer import HTTPWriter
from .writer import _create_coverage_client


log = get_logger(__name__)


class RuntimeCoverageWriter(HTTPWriter):
    """
    Writer for runtime request coverage data.

    This writer sends coverage spans to the CI Visibility coverage intake endpoint,
    reusing all the infrastructure from HTTPWriter including:
    - Connection pooling and management
    - Retry logic with backoff
    - Batching and encoding
    - Metrics and error handling
    """

    RETRY_ATTEMPTS = 3
    HTTP_METHOD = "POST"
    STATSD_NAMESPACE = "runtime_coverage.writer"

    def __init__(
        self,
        intake_url: Optional[str] = None,
        use_evp: bool = True,
        processing_interval: Optional[float] = None,
        timeout: Optional[float] = None,
    ):
        """
        Initialize the runtime coverage writer.

        Args:
            intake_url: Override intake URL (defaults to agent URL or agentless)
            use_evp: Whether to use EVP proxy mode (default: True)
            processing_interval: How often to flush buffered data (default: config value)
            timeout: Connection timeout (default: config value)
        """
        if processing_interval is None:
            processing_interval = config._trace_writer_interval_seconds
        if timeout is None:
            timeout = agent_config.agent_timeout_seconds

        # Determine intake URL and mode
        self._use_evp = use_evp
        if intake_url is None:
            if use_evp:
                intake_url = agent_config.trace_agent_url
            elif config._ci_visibility_agentless_url:
                intake_url = config._ci_visibility_agentless_url
                self._use_evp = False
            else:
                # Fallback to agent if no agentless URL configured
                intake_url = agent_config.trace_agent_url
                self._use_evp = True

        # Create coverage client - this is the only client we need
        # It will handle encoding spans with coverage data and sending to the right endpoint
        coverage_client = _create_coverage_client(
            use_evp=self._use_evp,
            intake_url=intake_url,
            itr_suite_skipping_mode=True,  # Enable ITR mode for proper encoding
        )

        super(RuntimeCoverageWriter, self).__init__(
            intake_url=intake_url,
            clients=[coverage_client],
            processing_interval=processing_interval,
            timeout=timeout,
            sync_mode=False,  # Always async for production
            reuse_connections=True,
            report_metrics=True,
        )

        log.debug(
            "RuntimeCoverageWriter initialized (use_evp=%s, intake_url=%s, interval=%s)",
            self._use_evp,
            intake_url,
            processing_interval,
        )

    def recreate(self) -> "RuntimeCoverageWriter":
        """
        Recreate the writer with the same configuration.

        This is required by HTTPWriter for certain scenarios like fork handling.
        """
        return self.__class__(
            intake_url=self.intake_url,
            use_evp=self._use_evp,
            processing_interval=self._interval,
            timeout=self._timeout,
        )


# Global singleton writer instance
_RUNTIME_COVERAGE_WRITER: Optional[RuntimeCoverageWriter] = None


def get_runtime_coverage_writer() -> Optional[RuntimeCoverageWriter]:
    """
    Get the global runtime coverage writer instance.

    Returns None if runtime coverage is not initialized.
    """
    return _RUNTIME_COVERAGE_WRITER


def initialize_runtime_coverage_writer() -> bool:
    """
    Initialize and start the global runtime coverage writer.

    This should be called once at startup if DD_CIVISIBILITY_REQUEST_COVERAGE is enabled.

    Returns:
        True if initialization was successful, False otherwise
    """
    global _RUNTIME_COVERAGE_WRITER

    if _RUNTIME_COVERAGE_WRITER is not None:
        log.debug("Runtime coverage writer already initialized")
        return True

    try:
        _RUNTIME_COVERAGE_WRITER = RuntimeCoverageWriter()
        _RUNTIME_COVERAGE_WRITER.start()
        log.info("Runtime coverage writer started successfully")
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
