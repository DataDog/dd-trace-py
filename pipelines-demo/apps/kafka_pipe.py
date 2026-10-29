"""kafka-pipe: consumes TOPIC_IN from Kafka and produces to TOPIC_OUT.

Every consumed message is produced on the same thread, so the tracer records
the in-process link (consume side -> produce side) inside this service.
"""

import os
import sys
import time


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from confluent_kafka import Consumer
from confluent_kafka import Producer


topic_in = os.environ.get("TOPIC_IN", "topic-a")
topic_out = os.environ.get("TOPIC_OUT", "topic-b")
group = os.environ.get("GROUP", "pipe-group")
target = int(os.environ.get("MSGS", "3"))

consumer = Consumer(
    {
        "bootstrap.servers": common.KAFKA_BOOTSTRAP,
        "group.id": group,
        "auto.offset.reset": "earliest",
    }
)
producer = Producer({"bootstrap.servers": common.KAFKA_BOOTSTRAP})
consumer.subscribe([topic_in])

piped = 0
deadline = time.time() + 60
while piped < target and time.time() < deadline:
    message = consumer.poll(1.0)
    if message is None or message.error():
        continue
    producer.produce(topic_out, message.value(), key=message.key())
    piped += 1
producer.flush(30)
consumer.close()
print("kafka-pipe piped %d messages %s -> %s (group %s)" % (piped, topic_in, topic_out, group))
