"""
The aio-pika integration traces RabbitMQ messaging performed with ``aio_pika``.

The integration is enabled automatically when using :ref:`ddtrace-run <ddtracerun>`
or :ref:`import ddtrace.auto <ddtraceauto>`::

    import ddtrace.auto
    import aio_pika

It can also be enabled manually::

    from ddtrace import patch

    patch(aio_pika=True)

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aio_pika["distributed_tracing"]

   Whether trace context is injected into and extracted from message headers.
   Configure this with ``DD_AIO_PIKA_DISTRIBUTED_TRACING``. It is disabled by
   default.

.. py:data:: ddtrace.config.aio_pika["service"]

   The service name used for aio-pika spans. By default, spans inherit the
   application service. Configure this with ``DD_AIO_PIKA_SERVICE`` or the
   compatibility alias ``DD_AIO_PIKA_SERVICE_NAME``.
"""

from .patch import get_version
from .patch import patch
from .patch import unpatch


__all__ = ["get_version", "patch", "unpatch"]
