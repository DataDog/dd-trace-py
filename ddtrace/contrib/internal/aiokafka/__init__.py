"""
This integration instruments the ``aiokafka<https://github.com/aio-libs/aiokafka>``
library to trace event streaming.

Enabling
~~~~~~~~

The aiokafka integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(aiokafka=True)
    import aiokafka
    ...

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aiokafka["service"]

   The service name reported by default for your kafka spans.

   This option can also be set with the ``DD_AIOKAFKA_SERVICE`` environment
   variable.

   Default: ``"kafka"``

.. py:data:: ddtrace.config.aiokafka["distributed_tracing_enabled"]

   Whether to enable distributed tracing between Kafka messages.

   This option can also be set with the ``DD_KAFKA_PROPAGATION_ENABLED`` environment
   variable.

   Default: ``"False"``

"""
