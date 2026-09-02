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

Configuration
~~~~~~~~~~~~~

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

.. py:data:: ddtrace.config.kafka["propagation_as_span_links"]

   Whether to attach propagated context as span links instead of continuing the
   producer's trace. When disabled (the default), the ``kafka.consume`` span becomes a
   child of the first consumed message's producer span. When enabled, the consume span
   starts a new trace and every consumed message's producer context is attached as a
   span link, so a batched ``consume()`` privileges no single producer. Requires
   ``distributed_tracing_enabled`` to be ``True``.

   This option can be set by adding ``kafka`` to the comma-separated
   ``DD_TRACE_PROPAGATION_AS_SPAN_LINKS`` environment variable.

   Default: ``False``

**Note**: `Data Streams Monitoring <https://docs.datadoghq.com/data_streams/>`_ (``DD_DATA_STREAMS_ENABLED=true``) or
distributed tracing (``DD_KAFKA_PROPAGATION_ENABLED=true``) will only work if Kafka message headers are supported.
If `log.message.format.version` is set in the Kafka broker configuration, it must be set to `0.11.0.0` or higher.
"""
