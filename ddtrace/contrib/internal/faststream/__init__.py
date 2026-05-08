"""
The faststream integration instruments the
`FastStream <https://github.com/ag2ai/faststream>`_ message-broker framework.
It traces consumer and producer operations across all FastStream brokers
(Kafka, Confluent, RabbitMQ, NATS, Redis) by registering a Datadog
middleware on every broker instance via FastStream's stable
``BrokerMiddleware`` extension point.


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
   into outgoing message headers and extracts it from incoming messages so
   that producer and consumer spans land in the same trace.

   This option can also be set with the ``DD_FASTSTREAM_DISTRIBUTED_TRACING``
   environment variable.

   Default: ``True``

.. note::

   Unlike the ``aiokafka`` integration which defaults distributed tracing to
   ``False``, FastStream defaults to ``True`` because FastStream applications
   tend to operate as services within a Datadog-traced mesh where end-to-end
   trace propagation is the expected behavior.


Span Model
~~~~~~~~~~

For every consumed message, the integration emits one span with operation
name ``<broker>.consume`` (e.g. ``kafka.consume``, ``rabbitmq.consume``).
For every published message, it emits one span with operation name
``<broker>.publish``. Both spans use ``span.type = "worker"`` and set
``span.kind`` to ``"consumer"`` or ``"producer"``.

Common tags emitted on every span:

- ``component`` — ``"faststream"``
- ``messaging.system`` — ``"kafka"``, ``"rabbitmq"``, ``"nats"``, ``"redis"``,
  ``"mqtt"``, or ``"faststream"`` (for unknown brokers)
- ``messaging.destination.name`` — topic / queue / subject / channel
- ``messaging.message_id`` — when present
- ``messaging.message.conversation_id`` — correlation id when present
- ``messaging.message.payload_size_bytes`` — body length when measurable
- ``_dd.measured`` — ``1``

Per-broker tags:

+----------+---------------------------------------------------------------+
| Broker   | Additional tags                                               |
+==========+===============================================================+
| kafka /  | ``messaging.kafka.partition``,                                |
| confluent| ``messaging.kafka.message.offset``,                           |
|          | ``messaging.kafka.message.key``,                              |
|          | ``messaging.kafka.destination.partition`` (publish),          |
|          | ``messaging.kafka.bootstrap.servers``,                        |
|          | ``kafka.group_id`` (consume),                                 |
|          | ``kafka.tombstone`` (publish, ``True`` when body is ``None``),|
|          | ``kafka.received_message`` (consume)                          |
+----------+---------------------------------------------------------------+
| rabbitmq | ``messaging.rabbitmq.destination.routing_key``,               |
|          | ``messaging.rabbitmq.message.delivery_tag``                   |
+----------+---------------------------------------------------------------+
| nats     | ``messaging.batch.message_count`` (batch consumes); each      |
|          | record's distributed-trace context is added as a span link.   |
+----------+---------------------------------------------------------------+
| redis    | ``messaging.batch.message_count`` (when ``type`` indicates    |
|          | a batch).                                                     |
+----------+---------------------------------------------------------------+

The span ``resource`` is set to the destination (topic/queue/subject), so
Datadog APM groups all messages targeting the same destination together.


Data Streams Monitoring
~~~~~~~~~~~~~~~~~~~~~~~

The integration emits DSM pathway checkpoints on publish and consume when
``DD_DATA_STREAMS_ENABLED=true`` is set. The ``dd-pathway-ctx`` header is
injected into outgoing messages alongside the trace-context header so that
downstream consumers can chain their checkpoints to form an end-to-end
pathway. Edge tags use the ``topic:`` / ``subject:`` / ``channel:`` key
appropriate for the broker (matching aiokafka and kombu conventions).

"""

from .patch import get_version
from .patch import patch
from .patch import unpatch


__all__ = ["patch", "unpatch", "get_version"]
