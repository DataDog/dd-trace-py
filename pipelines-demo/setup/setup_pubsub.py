"""Create the Pub/Sub topics and subscriptions used by the demo topologies.

Each subscription is created from its topic with a fixed id; the apps call
get_subscription at startup so the tracer learns the subscription -> topic
mapping.

Run with plain python3 (NOT ddtrace-run) so no sides are recorded:
    python3 setup/setup_pubsub.py
"""

import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from google.api_core.exceptions import AlreadyExists
from google.cloud import pubsub_v1


# (topic, subscription) pairs: subscription ids are the demo group names.
PAIRS = [("ps-topic-a", "ps-sub-a"), ("ps-topic-b", "ps-sub-b")]

project = common.PUBSUB_PROJECT
publisher = pubsub_v1.PublisherClient()
subscriber = pubsub_v1.SubscriberClient()

for topic, subscription in PAIRS:
    topic_path = publisher.topic_path(project, topic)
    subscription_path = subscriber.subscription_path(project, subscription)
    try:
        publisher.create_topic(request={"name": topic_path})
    except AlreadyExists:  # from an earlier run
        pass
    try:
        subscriber.create_subscription(request={"name": subscription_path, "topic": topic_path})
    except AlreadyExists:  # from an earlier run
        pass
    print("pubsub wired: %s -> subscription %s" % (topic, subscription))
