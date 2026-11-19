"""
This integration instruments the ``kafka-python<https://github.com/dpkp/kafka-python>``
library to trace message production and consumption, and to report Data Streams Monitoring
checkpoints and consumer offsets.

Enabling
~~~~~~~~

The kafka-python integration is enabled automatically when using
:ref:`ddtrace-run<ddtracerun>` or :ref:`import ddtrace.auto<ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(kafka_python=True)
    import kafka
    ...

Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.kafka_python["service"]

   The service name reported by default for your kafka-python spans.

   This option can also be set with the ``DD_KAFKA_PYTHON_SERVICE`` environment
   variable.

   Default: ``"kafka"``

.. py:data:: ddtrace.config.kafka_python["distributed_tracing_enabled"]

   Whether to enable distributed tracing between Kafka messages.

   This option can also be set with the ``DD_KAFKA_PROPAGATION_ENABLED`` environment
   variable.

   Default: ``"False"``

"""
