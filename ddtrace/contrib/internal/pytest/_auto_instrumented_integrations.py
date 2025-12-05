"""
Auto-instrumentation for pytest plugins.

This module provides automatic detection and patching of supported
integrations when running tests with ddtrace.
"""

import os

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


# Plugin name constants - single source of truth
PLUGIN_PLAYWRIGHT = "playwright"
# Future plugins can be added here:
# PLUGIN_SELENIUM = "selenium"
# PLUGIN_HTTPX = "httpx"


def auto_instrument_playwright(config, detected_plugins):
    """
    Automatically instrument Playwright if detected in the plugin set.

    This provides a zero-configuration experience for Playwright users.
    Users can opt-out by setting DD_TRACE_PLAYWRIGHT_ENABLED=false.

    Args:
        config: pytest Config object
        detected_plugins: Set of detected plugin names to instrument
    """
    # Only proceed if Playwright is in the detected plugins
    if PLUGIN_PLAYWRIGHT not in detected_plugins:
        return

    # Check if user explicitly disabled auto-instrumentation
    if os.getenv("DD_TRACE_PLAYWRIGHT_ENABLED", "").lower() == "false":
        log.debug("Playwright auto-instrumentation disabled via DD_TRACE_PLAYWRIGHT_ENABLED")
        return

    # Patch Playwright
    try:
        from ddtrace import patch

        # Note: The playwright integration has its own double-patch protection
        # via playwright._datadog_patch flag, so we can safely call this
        patch(playwright=True)

        log.debug("Enabled Playwright instrumentation")
    except Exception as e:
        # Don't fail tests if patching fails
        log.warning("Failed to auto-instrument Playwright: %s", e, exc_info=True)


def auto_instrument_integrations(config, detected_plugins):
    """
    Auto-instrument all supported integrations.

    This is called from pytest_configure and will automatically detect
    and patch supported test integrations like Playwright.

    Note: This should only be called when --ddtrace-patch-all is NOT used,
    since that already patches everything. The caller is responsible for
    checking this condition.

    Args:
        config: pytest Config object
        detected_plugins: Set of detected plugin names to instrument
    """
    # Auto-instrument Playwright
    auto_instrument_playwright(config, detected_plugins)

    # Future: Add auto-instrumentation for other integrations here
    # auto_instrument_selenium(config, detected_plugins)
    # auto_instrument_httpx(config, detected_plugins)
