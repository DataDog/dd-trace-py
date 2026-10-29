"""kafka-sink: consumes a Kafka topic (pipeline end).

Also reused for the star and fan-out topologies: run it once per consumer
service with distinct DD_SERVICE and GROUP environment variables (see
PIPELINE_TOPOLOGIES.md).
"""

import os
import sys
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from confluent_kafka import Consumer


topic = os.environ.get("TOPIC", "topic-b")
group = os.environ.get("GROUP", "sink-group")
target = int(os.environ.get("MSGS", "3"))

consumer = Consumer(
    {
        "bootstrap.servers": common.KAFKA_BOOTSTRAP,
        "group.id": group,
        "auto.offset.reset": "earliest",
    }
)
consumer.subscribe([topic])

received = 0
deadline = time.time() + 60
while received < target and time.time() < deadline:
    message = consumer.poll(1.0)
    if message is None or message.error():
        continue
    received += 1
consumer.close()
print("kafka-sink received %d messages from %s (group %s)" % (received, topic, group))
