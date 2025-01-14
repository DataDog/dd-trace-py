"""
The Avro integration will trace all Avro read / write calls made with the ``avro``
library. This integration is enabled by default.

Enabling
~~~~~~~~

The avro integration is enabled by default. Use
:func:`patch()<ddtrace.patch>` to enable the integration::

    from ddtrace import patch
    patch(avro=True)

Configuration
~~~~~~~~~~~~~

"""

from ddtrace.internal.utils.deprecations import DDTraceDeprecationWarning
from ddtrace.vendor.debtcollector import deprecate

from ..internal.avro.patch import get_version  # noqa: F401
from ..internal.avro.patch import patch  # noqa: F401


deprecate(
    ("%s is deprecated" % (__name__)),
    message="Avoid using this package directly. "
    "Use ``ddtrace.auto`` or the ``ddtrace-run`` command to enable and configure this integration.",
    category=DDTraceDeprecationWarning,
    removal_version="3.0.0",
)
