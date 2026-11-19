import uuid

from kafka import KafkaConsumer
from kafka import KafkaProducer
from kafka.admin import KafkaAdminClient
from kafka.admin import NewTopic

from tests.contrib.config import KAFKA_CONFIG


BOOTSTRAP_SERVERS = f"{KAFKA_CONFIG['host']}:{KAFKA_CONFIG['port']}"
GROUP_ID = "test_group"
KEY = b"test_key"
PAYLOAD = b"hueh hueh hueh"


def create_topic(prefix):
    name = f"{prefix}_{uuid.uuid4().hex[:8]}"
    admin = KafkaAdminClient(bootstrap_servers=BOOTSTRAP_SERVERS)
    try:
        admin.create_topics([NewTopic(name, num_partitions=1, replication_factor=1)])
    finally:
        admin.close()
    return name


def make_producer():
    return KafkaProducer(bootstrap_servers=BOOTSTRAP_SERVERS)


def make_consumer(topic, enable_auto_commit=False):
    return KafkaConsumer(
        topic,
        bootstrap_servers=BOOTSTRAP_SERVERS,
        group_id=GROUP_ID,
        auto_offset_reset="earliest",
        enable_auto_commit=enable_auto_commit,
        consumer_timeout_ms=15000,
    )


def poll_records(consumer, count):
    records = []
    for _ in range(30):
        for batch in consumer.poll(timeout_ms=1000).values():
            records.extend(batch)
        if len(records) >= count:
            break
    return records


def iterate_records(consumer, count):
    records = []
    for record in consumer:
        records.append(record)
        if len(records) >= count:
            break
    return records
