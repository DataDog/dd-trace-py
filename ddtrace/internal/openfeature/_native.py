"""
Native interface for FFE (Feature Flagging and Experimentation) processing.

This module provides the interface to the PyO3 native function that processes
feature flag configuration rules.
"""

from typing import Optional

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

is_available = True

try:
    from ddtrace.internal.native._native import ffe
except ImportError:
    is_available = False
    log.debug("FFE native module not available, feature flag processing disabled")


def process_ffe_configuration(config_bytes: bytes) -> Optional[ffe.Configuration]:
    """
    Process feature flag configuration by forwarding raw bytes to native function.

    Args:
        config_bytes: Raw bytes from Remote Configuration payload

    Returns:
        True if processing was successful, False otherwise
    """
    if not is_available:
        log.debug("FFE native module not available, skipping configuration")
        return None

    try:
        return ffe.Configuration(config_bytes)
    except Exception as e:
        log.error("Error processing FFE configuration: %s", e, exc_info=True)
        return None
