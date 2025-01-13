"""
The freezegun integration reconfigures freezegun's default ignore list to ignore ddtrace.

Enabling
~~~~~~~~
Use DD_TRACE_<INTEGRATION>_ENABLED environment variable to enable or disable this integration.

Configuration
~~~~~~~~~~~~~
The freezegun integration is not configurable, but may be disabled using DD_PATCH_MODULES=freezegun:false .
"""

# Expose public methods
from ..internal.freezegun.patch import get_version
from ..internal.freezegun.patch import patch
from ..internal.freezegun.patch import unpatch


__all__ = ["get_version", "patch", "unpatch"]
