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


To configure the kafka integration using the
``Pin`` API::

    from ddtrace.trace import Pin
    from ddtrace import patch

    # Make sure to patch before importing confluent_kafka
    patch(kafka=True)

    import confluent_kafka

    Pin.override(confluent_kafka, service="custom-service-name")
"""
