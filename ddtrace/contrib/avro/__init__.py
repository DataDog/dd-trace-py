"""
The Avro integration will trace all Avro read / write calls made with the ``avro``
library. This integration is enabled by default.

Enabling
~~~~~~~~

The avro integration is enabled by default.
Use DD_TRACE_<INTEGRATION>_ENABLED environment variable to enable or disable this integration.

Configuration
~~~~~~~~~~~~~

"""
# Expose public methods
from ..internal.avro.patch import get_version
from ..internal.avro.patch import patch


__all__ = ["patch", "get_version"]
