"""
The Temporal integration traces workflow and activity operations performed with
the ``temporalio`` library.

The integration traces workflow starts and signals as producer operations,
workflow queries as client operations, and activity execution as consumer
operations. Trace context is propagated through Temporal headers from workflow
starts to activities. Workflow execution itself is not traced because Temporal
may replay workflow code.


Enabling
~~~~~~~~

The ``temporalio`` integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Alternatively, use :func:`patch()<ddtrace.patch>` to manually enable the
integration::

    from ddtrace import patch

    patch(temporalio=True)


Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.temporalio["service"]

   The service name reported for Temporal spans.

   This option can also be set with the ``DD_TEMPORALIO_SERVICE`` environment
   variable.

   Default: ``"temporalio"``

.. py:data:: ddtrace.config.temporalio["distributed_tracing"]

   Propagate trace context through Temporal headers.

   This option can also be set with the
   ``DD_TEMPORALIO_DISTRIBUTED_TRACING`` environment variable.

   Default: ``True``
"""

from ddtrace import config
from ddtrace.internal.schema import schematize_service_name
from ddtrace.internal.settings._config import _get_config
from ddtrace.internal.utils.formats import asbool


config._add(
    "temporalio",
    {
        # Schema functions are selected dynamically and are untyped.
        "_default_service": schematize_service_name("temporalio"),  # type: ignore[operator]
        "distributed_tracing": asbool(_get_config("DD_TEMPORALIO_DISTRIBUTED_TRACING", default=True)),
    },
)  # type: ignore[no-untyped-call]


import ddtrace._trace.subscribers.temporalio  # noqa: E402,F401
