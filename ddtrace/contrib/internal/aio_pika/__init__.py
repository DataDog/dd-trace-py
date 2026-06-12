"""
This integration instruments the ``aio-pika<https://github.com/mosquito/aio-pika>``
library to trace AMQP messaging via RabbitMQ.

Enabling
~~~~~~~~

The aio_pika integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aio_pika=True)
    import aio_pika
    ...

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aio_pika["service"]

   The service name reported by default for your aio_pika spans.

   This option can also be set with the ``DD_AIO_PIKA_SERVICE`` environment
   variable.

   Default: ``"aio_pika"``

.. py:data:: ddtrace.config.aio_pika["distributed_tracing_enabled"]

   Whether to enable distributed tracing between producer and consumer spans.
   When enabled, trace context is injected into message headers on publish and
   extracted from message headers on consume.

   This option can also be set with the ``DD_AIO_PIKA_DISTRIBUTED_TRACING``
   environment variable.

   Default: ``True``

"""
