"""
The ``urllib3`` integration instruments tracing on http calls with optional
support for distributed tracing across services the client communicates with.


Enabling
~~~~~~~~

The ``urllib3`` integration is not enabled by default. Use ``patch_all()``
with the environment variable ``DD_TRACE_URLLIB3_ENABLED`` set, or call
:func:`patch()<ddtrace.patch>` with the ``urllib3`` argument set to ``True`` to manually
enable the integration, before importing and using ``urllib3``::

    from ddtrace import patch
    patch(urllib3=True)

    # use urllib3 like usual


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.urllib3['service']

   The service name reported by default for urllib3 client instances.

   This option can also be set with the ``DD_URLLIB3_SERVICE`` environment
   variable.

   Default: ``"urllib3"``


.. py:data:: ddtrace.config.urllib3['distributed_tracing']

   Whether or not to parse distributed tracing headers.

   Default: ``True``


.. py:data:: ddtrace.config.urllib3['trace_query_string']

   Whether or not to include the query string as a tag.

   Default: ``False``


.. py:data:: ddtrace.config.urllib3['split_by_domain']

   Whether or not to use the domain name of requests as the service name.

   Default: ``False``
"""
from ...internal.utils.importlib import require_modules
from .patch import get_version
from .patch import patch
from .patch import unpatch


required_modules = ["urllib3"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:

        __all__ = ["patch", "unpatch", "get_version"]
