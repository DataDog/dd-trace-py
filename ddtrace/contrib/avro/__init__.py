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

from ..internal.avro.patch import get_version  # noqa: F401
from ..internal.avro.patch import patch  # noqa: F401
