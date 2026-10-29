"""pubsub-pipe: consumes subscription SUB_IN and produces to topic TOPIC_OUT.

get_subscription is called at startup so the tracer learns the subscription ->
topic mapping (CONTRACT.md section 1 pubsub notes): the consume side is
recorded against the backing TOPIC with the subscription id as group. Each
pulled message is published from the streaming-pull callback thread that
recorded the consume, so the in-process link is captured.
"""

import os
import sys
import threading


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import common
from google.cloud import pubsub_v1


project = common.PUBSUB_PROJECT
subscription_in = os.environ.get("SUB_IN", "ps-sub-a")
topic_out = os.environ.get("TOPIC_OUT", "ps-topic-b")
target = int(os.environ.get("MSGS", "3"))

subscriber = pubsub_v1.SubscriberClient()
subscription_path = subscriber.subscription_path(project, subscription_in)
# The tracer hook uses this same call to learn the backing topic.
subscription = subscriber.get_subscription(request={"subscription": subscription_path})
topic_in = subscription.topic.rsplit("/", 1)[-1]

publisher = pubsub_v1.PublisherClient()
publish_path = publisher.topic_path(project, topic_out)

done = threading.Event()
received = {"count": 0}


def handle(message):
    publisher.publish(publish_path, message.data)
    message.ack()
    received["count"] += 1
    if received["count"] >= target:
        done.set()


future = subscriber.subscribe(subscription_path, handle)
done.wait(120)
future.cancel()
print("pubsub-pipe piped %d messages %s -> %s" % (received["count"], topic_in, topic_out))
