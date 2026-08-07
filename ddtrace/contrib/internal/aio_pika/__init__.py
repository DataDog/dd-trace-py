"""Instrument aio-pika to report RabbitMQ messaging.

``import ddtrace.auto`` will automatically patch your aio-pika client to make it work.

::

    import ddtrace.auto
    import aio_pika

    async def main():
        connection = await aio_pika.connect("amqp://guest:guest@localhost/")
        channel = await connection.channel()
        exchange = await channel.declare_exchange("my_exchange", aio_pika.ExchangeType.DIRECT)
        await exchange.publish(
            aio_pika.Message(body=b"Hello"),
            routing_key="my_key",
        )

Configuration
~~~~~~~~~~~~~

.. py:data:: ddtrace.config.aio_pika["service"]

   The service name reported by aio-pika spans. This option can also be set
   with the ``DD_AIO_PIKA_SERVICE`` environment variable. The
   ``DD_AIO_PIKA_SERVICE_NAME`` environment variable is supported as an alias.

   Default: the application service name.

.. py:data:: ddtrace.config.aio_pika["distributed_tracing_enabled"]

   Whether to inject/extract distributed tracing headers into/from message headers.

   This option can also be set with the ``DD_AIO_PIKA_DISTRIBUTED_TRACING`` environment
   variable.

   Default: ``False``
"""
