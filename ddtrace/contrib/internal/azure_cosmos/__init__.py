"""
The azure_cosmos integration instruments the CRUD operations of the
Azure CosmosDB library.


Enabling
~~~~~~~~

The azure_cosmos integration is enabled automatically when using
:ref:`ddtrace-run <ddtracerun>` or :ref:`import ddtrace.auto <ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(azure_cosmos=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.azure_cosmos["service"]

   The service name reported by default for azure_cosmos clients.

   Default: ``"azure_cosmos"``

"""

from .patch import get_version
from .patch import patch
from .patch import unpatch


__all__ = ["patch", "unpatch", "get_version"]
