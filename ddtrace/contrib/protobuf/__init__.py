"""
The Protobuf integration will trace all Protobuf read / write calls made with the ``google.protobuf``
library. This integration is enabled by default.

Enabling
~~~~~~~~

The protobuf integration is enabled by default. Use
:func:`patch()<ddtrace.patch>` to enable the integration::

    from ddtrace import patch
    patch(protobuf=True)

Configuration
~~~~~~~~~~~~~

"""
from ..internal.protobuf.patch import get_version  # noqa: F401
from ..internal.protobuf.patch import patch  # noqa: F401
from ..internal.protobuf.patch import unpatch  # noqa: F401
