import aio_pika

from tests.contrib.config import RABBITMQ_CONFIG


async def run_pull_flow(queue_name):
    url = "amqp://{user}:{password}@{host}:{port}/".format(**RABBITMQ_CONFIG)
    connection = await aio_pika.connect(url)
    try:
        channel = await connection.channel()
        queue = await channel.declare_queue(queue_name, durable=True)
        message = aio_pika.Message(b"payload", message_id="snapshot-message")
        try:
            confirmation = await channel.default_exchange.publish(message, routing_key=queue.name)
            incoming = await queue.get(timeout=2)
            assert incoming is not None
            async with incoming.process():
                pass
            assert confirmation is not None
        finally:
            await queue.delete(if_unused=False, if_empty=False)
    finally:
        await connection.close()
