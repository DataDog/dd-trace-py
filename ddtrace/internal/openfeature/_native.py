"""
Native interface for FFAndE (Feature Flagging and Experimentation) processing.

This module provides the interface to the PyO3 native function that processes
feature flag configuration rules.
"""
from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

is_available = True

try:
    from ddtrace.internal.native._native import ffande_process_config
except ImportError:
    is_available = False
    log.debug("FFAndE native module not available, feature flag processing disabled")

    # Provide a no-op fallback
    def ffande_process_config(config_bytes: bytes) -> Optional[bool]:
        """Fallback implementation when native module is not available."""
        log.warning("FFE native module not available, ignoring configuration")
        return None


def process_ffe_configuration(config_bytes: bytes) -> bool:
    """
    Process feature flag configuration by forwarding raw bytes to native function.

    Args:
        config_bytes: Raw bytes from Remote Configuration payload

    Returns:
        True if processing was successful, False otherwise
    """
    if not is_available:
        log.debug("FFAndE native module not available, skipping configuration")
        return False

    try:
        result = ffande_process_config(config_bytes)
        if result is None:
            log.debug("FFAndE native processing returned None")
            return False
        return result
    except Exception as e:
        log.debug("Error processing FFE configuration: %s", e, exc_info=True)
        return False
