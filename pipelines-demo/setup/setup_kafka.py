"""Create the Kafka topics used by the demo topologies.

Run with plain python3 (NOT ddtrace-run) so no sides are recorded:
    python3 setup/setup_kafka.py
"""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from confluent_kafka.admin import AdminClient
from confluent_kafka.admin import NewTopic


TOPICS = ["topic-a", "topic-b", "topic-c"]

admin = AdminClient({"bootstrap.servers": common.KAFKA_BOOTSTRAP})
futures = admin.create_topics([NewTopic(t, num_partitions=1, replication_factor=1) for t in TOPICS])
for topic, future in futures.items():
    try:
        future.result()
        print("kafka topic created: %s" % topic)
    except Exception as exc:  # already exists from an earlier run: fine
        print("kafka topic %s: %s" % (topic, exc))
