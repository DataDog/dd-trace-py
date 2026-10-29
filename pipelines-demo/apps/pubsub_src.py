"""pubsub-src: publishes demo messages to a Pub/Sub topic (pipeline source)."""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from google.cloud import pubsub_v1


project = common.PUBSUB_PROJECT
topic = os.environ.get("TOPIC", "ps-topic-a")
count = int(os.environ.get("MSGS", "3"))

publisher = pubsub_v1.PublisherClient()
topic_path = publisher.topic_path(project, topic)
futures = [publisher.publish(topic_path, ("msg-%d" % i).encode()) for i in range(count)]
for future in futures:
    future.result()
print("pubsub-src published %d messages to %s" % (count, topic))
