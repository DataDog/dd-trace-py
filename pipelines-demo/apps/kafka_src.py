"""kafka-src: produces demo messages to a Kafka topic (pipeline source).

Run under ddtrace-run: the kafka produce hook records the ``kafka`` produce
side and the source link (in_side = -1) for this process.
"""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from confluent_kafka import Producer


topic = os.environ.get("TOPIC", "topic-a")
count = int(os.environ.get("MSGS", "3"))

producer = Producer({"bootstrap.servers": common.KAFKA_BOOTSTRAP})
for i in range(count):
    producer.produce(topic, "msg-%d" % i, key="k-%d" % i)
producer.flush(30)
print("kafka-src produced %d messages to %s" % (count, topic))
