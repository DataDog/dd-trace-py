"""
This integration instruments the ``confluent-kafka<https://github.com/confluentinc/confluent-kafka-python>``
library to trace event streaming.

Enabling
~~~~~~~~

The kafka integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(kafka=True)
    import confluent_kafka
    ...

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.kafka["service"]

   The service name reported by default for your kafka spans.

   This option can also be set with the ``DD_KAFKA_SERVICE`` environment
   variable.

   Default: ``"kafka"``

.. py:data:: ddtrace.config.kafka["distributed_tracing_enabled"]

   Whether to enable distributed tracing between Kafka messages.

   This option can also be set with the ``DD_KAFKA_PROPAGATION_ENABLED`` environment
   variable.

   Default: ``"False"``


To configure the kafka integration using the
``Pin`` API::

    from ddtrace.trace import Pin
    from ddtrace import patch

    # Make sure to patch before importing confluent_kafka
    patch(kafka=True)

    import confluent_kafka

    Pin.override(confluent_kafka, service="custom-service-name")

**Note**: `Data Streams Monitoring <https://docs.datadoghq.com/data_streams/>`_ (``DD_DATA_STREAMS_ENABLED=true``) or
distributed tracing (``DD_KAFKA_PROPAGATION_ENABLED=true``) will only work if Kafka message headers are supported.
If `log.message.format.version` is set in the Kafka broker configuration, it must be set to `0.11.0.0` or higher.
"""
