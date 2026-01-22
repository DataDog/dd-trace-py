"""
The azure_cosmos integration instruments the CRUD operations of the
Azure CosmosDB library.


Enabling
~~~~~~~~

The azure_cosmos integration is enabled by default when using
:ref:`import ddtrace.auto <ddtraceauto>`.

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.azure_cosmos["service"]

   The service name reported by default for azure_cosmos clients.

   This option can also be set with the ``DD_AZURE_COSMOS_SERVICE`` environment
   variable.

   Default: ``"azure_cosmos"``

"""

from .patch import get_version
from .patch import patch
from .patch import unpatch


__all__ = ["patch", "unpatch", "get_version"]
