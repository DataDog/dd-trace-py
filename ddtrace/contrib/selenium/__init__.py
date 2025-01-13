"""
The Selenium integration enriches Test Visibility data with extra tags and, if available,
Real User Monitoring session replays.

Enabling
~~~~~~~~

The Selenium integration is enabled by default in test contexts (eg: pytest, or unittest).
Use DD_TRACE_<INTEGRATION>_ENABLED environment variable to enable or disable this integration.

When using pytest, the `--ddtrace-patch-all` flag is required in order for this integration to
be enabled.

Configuration
~~~~~~~~~~~~~

The Selenium integration can be configured using the following options:

DD_CIVISIBILITY_RUM_FLUSH_WAIT_MILLIS: The time in milliseconds to wait after flushing the RUM session.
"""
# Expose public methods
from ..internal.selenium.patch import get_version
from ..internal.selenium.patch import patch
from ..internal.selenium.patch import unpatch


__all__ = ["get_version", "patch", "unpatch"]
