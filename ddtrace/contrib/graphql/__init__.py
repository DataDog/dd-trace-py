"""
The graphql integration instruments graphql requests. Version 2.0 and above are fully
supported.


Enabling
~~~~~~~~

The graphql integration is enabled automatically when using
:ref:`ddtrace-run <ddtracerun>` or :func:`patch_all() <ddtrace.patch_all>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(graphql=True)


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.graphql["service"]

   The service name reported by default for graphql instances.

   This option can also be set with the ``DD_GRAPHQL_SERVICE`` environment
   variable.

   Default: ``"graphql"``


Instance Configuration
~~~~~~~~~~~~~~~~~~~~~~

To configure the graphql integration on a per-instance basis use the
``Pin`` API::

    import graphql
    from ddtrace import Pin

    mygraphql = graphql.graphql(...)
    Pin.override(mygraphql, service="mygraphql")
"""
from ...internal.utils.importlib import require_modules


required_modules = ["graphql"]

with require_modules(required_modules) as missing_modules:
    if not missing_modules:
        from .patch import patch
        from .patch import unpatch

        __all__ = ["patch", "unpatch"]
