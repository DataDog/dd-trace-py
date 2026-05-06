"""
The faststream integration instruments the
`FastStream <https://github.com/ag2ai/faststream>`_ message-broker framework.
It traces consumer and producer operations across all FastStream brokers
(Kafka, Confluent, RabbitMQ, NATS, Redis, MQTT) by registering a Datadog
middleware on every broker instance.


Enabling
~~~~~~~~

The faststream integration is enabled automatically when using
:ref:`ddtrace-run <ddtracerun>` or :ref:`import ddtrace.auto <ddtraceauto>`.

Or use :func:`patch() <ddtrace.patch>` to manually enable the integration::

    from ddtrace import patch
    patch(faststream=True)
    from faststream.kafka import KafkaBroker

    broker = KafkaBroker("localhost:9092")


Global Configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.faststream["service"]

   The service name reported by default for FastStream spans.

   This option can also be set with the ``DD_FASTSTREAM_SERVICE`` environment
   variable.

   Default: ``"faststream"``

.. py:data:: ddtrace.config.faststream["distributed_tracing_enabled"]

   Whether to propagate distributed-tracing context through message headers.
   When enabled (default), the integration injects the active trace context
   into outgoing message headers and extracts it from incoming messages.

   This option can also be set with the ``DD_FASTSTREAM_DISTRIBUTED_TRACING``
   environment variable.

   Default: ``True``

"""

from .patch import get_version
from .patch import patch
from .patch import unpatch


__all__ = ["patch", "unpatch", "get_version"]
