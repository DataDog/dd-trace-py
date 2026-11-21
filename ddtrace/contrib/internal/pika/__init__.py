"""
This integration instruments the `pika <https://pika.readthedocs.io/>`_ RabbitMQ client.

It traces ``basic_publish`` and message delivery to ``basic_consume`` callbacks,
``basic_get`` and ``BlockingChannel.consume``, for every pika connection adapter.
Trace context is propagated in the message headers. When Data Streams Monitoring is
enabled, it also sets pathway checkpoints on publish and consume.

Enabling
~~~~~~~~

The pika integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(pika=True)
    import pika
    ...

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pika["service"]

   The service name reported by default for pika spans.

   This option can also be set with the ``DD_PIKA_SERVICE`` environment
   variable.

   Default: ``"rabbitmq"``

.. py:data:: ddtrace.config.pika["distributed_tracing_enabled"]

   Whether to propagate trace context in message headers and continue it on consume.

   This option can also be set with the ``DD_PIKA_DISTRIBUTED_TRACING`` environment
   variable.

   Default: ``True``
"""
