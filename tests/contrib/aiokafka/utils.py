from contextlib import asynccontextmanager
import logging

import aiokafka
from aiokafka.admin import AIOKafkaAdminClient
from aiokafka.admin import NewTopic

from tests.contrib.config import KAFKA_CONFIG


logger = logging.getLogger(__name__)

GROUP_ID = "test_group"
BOOTSTRAP_SERVERS = f"127.0.0.1:{KAFKA_CONFIG['port']}"
KEY = "test_key".encode("utf-8")
PAYLOAD = "hueh hueh hueh".encode("utf-8")
ENABLE_AUTO_COMMIT = True


def has_header(headers, header_name):
    """Check if a header with the given name exists in the headers list."""
    return any(header[0] == header_name for header in headers)


def find_span_by_name(spans, span_name):
    """Find the first span with the given name in a list of spans."""
    return next((span for span in spans if span.name == span_name), None)


async def create_topic(topic_name):
    """Create a Kafka topic for testing"""
    logger.debug("Creating topic %s", topic_name)

    client = AIOKafkaAdminClient(bootstrap_servers=[BOOTSTRAP_SERVERS])
    await client.start()

    try:
        await client.delete_topics([topic_name])
        await client.create_topics([NewTopic(topic_name, 1, 1)])
    except Exception as e:
        logger.error("Failed to delete/create topic %s: %s", topic_name, e)
    finally:
        await client.close()

    return topic_name


@asynccontextmanager
async def producer_ctx(bootstrap_servers):
    """Context manager for producer - automatically starts and stops"""
    producer = aiokafka.AIOKafkaProducer(bootstrap_servers=bootstrap_servers)
    await producer.start()
    try:
        yield producer
    finally:
        await producer.stop()


@asynccontextmanager
async def consumer_ctx(topics, enable_auto_commit=ENABLE_AUTO_COMMIT):
    """Context manager for consumer - automatically starts and stops"""
    consumer = aiokafka.AIOKafkaConsumer(
        bootstrap_servers=[BOOTSTRAP_SERVERS],
        auto_offset_reset="earliest",
        group_id=GROUP_ID,
        enable_auto_commit=enable_auto_commit,
    )
    consumer.subscribe(topics)
    await consumer.start()
    try:
        yield consumer
    finally:
        await consumer.stop()
