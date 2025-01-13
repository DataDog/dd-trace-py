"""
The Protobuf integration will trace all Protobuf read / write calls made with the ``google.protobuf``
library. This integration is enabled by default.

Enabling
~~~~~~~~

The protobuf integration is enabled by default.
Use DD_TRACE_<INTEGRATION>_ENABLED environment variable to enable or disable this integration.

Configuration
~~~~~~~~~~~~~

"""
# Expose public methods
from ..internal.protobuf.patch import get_version
from ..internal.protobuf.patch import patch
from ..internal.protobuf.patch import unpatch


__all__ = ["patch", "unpatch", "get_version"]
