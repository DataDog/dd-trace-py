"""
The freezegun integration reconfigures freezegun's default ignore list to ignore ddtrace.

Enabling
~~~~~~~~
The freezegun integration is enabled by default. Use :func:`patch()<ddtrace.patch>` to enable the integration::
    from ddtrace import patch
    patch(freezegun=True)


Configuration
~~~~~~~~~~~~~
The freezegun integration is not configurable, but may be disabled using DD_PATCH_MODULES=freezegun:false .
"""


from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from ..internal.freezegun.patch import get_version  # noqa: F401
from ..internal.freezegun.patch import patch  # noqa: F401
from ..internal.freezegun.patch import unpatch  # noqa: F401


deprecate(
    ("%s is deprecated" % (__name__)),
    message="Avoid using this package directly. "
    "Use ``ddtrace.auto`` or the ``ddtrace-run`` command to enable and configure this integration.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
