"""
This integration instruments ``graphql-core`` queries.

Enabling
~~~~~~~~

The graphql integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(graphql=True)
    import graphql
    ...

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.graphql["service"]

   The service name reported by default for graphql instances.

   This option can also be set with the ``DD_SERVICE`` environment
   variable.

   Default: ``"graphql"``

.. py:data:: ddtrace.config.graphql["resolvers_enabled"]

   To enable ``graphql.resolve`` spans set ``DD_TRACE_GRAPHQL_RESOLVERS_ENABLED`` to True

   Default: ``False``

   Enabling instrumentation for resolvers will produce a ``graphql.resolve`` span for every graphql field.
   For complex graphql queries this could produce large traces.


To configure the graphql integration using the
``Pin`` API::

    from ddtrace.trace import Pin
    import graphql

    Pin.override(graphql, service="mygraphql")
"""


# Required to allow users to import from  `ddtrace.contrib.graphql.patch` directly
import warnings as _w


with _w.catch_warnings():
    _w.simplefilter("ignore", DeprecationWarning)
    from . import patch as _  # noqa: F401, I001


from ddtrace.contrib.internal.graphql.patch import get_version  # noqa: F401
from ddtrace.contrib.internal.graphql.patch import patch  # noqa: F401
from ddtrace.contrib.internal.graphql.patch import unpatch  # noqa: F401
